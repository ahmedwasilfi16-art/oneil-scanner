# بوت سكالبنج ORB + VWAP
# بيشتغل الصبح: يلقط الأسهم اللي في الملعب، ويراقبها كل دقيقة، ويبعت إشارات الدخول والخروج على تليجرام

import datetime as dt
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from news import news_summary

NY = ZoneInfo("America/New_York")

CFG = {
    "acct": 6000.0,          # رأس المال $
    "risk_pct": 1.0,         # مخاطرة كل صفقة %
    "max_pos": 50.0,         # أقصى حجم للصفقة % من رأس المال
    "rr": 2.0,               # الهدف = ضعف المخاطرة
    "min_rvol": 1.5,         # فوليوم شمعة الاختراق مقابل متوسط الشموع اللي قبلها
    "last_entry": "11:00",   # آخر وقت لإشارة دخول
    "end": "11:10",          # البوت يقف الساعة دي
    "gap_min": 3.0,          # أقل فجوة في البري ماركت %
    "pm_min_vol": 50_000,    # أقل فوليوم في البري ماركت
    "max_avoid": 5,          # عدد الأسهم النازلة اللي يحذرك منها
    "min_price": 2.0,        # أقل سعر للسهم
    "min_change": 3.0,       # أقل نسبة صعود النهارده %
    "min_avg_vol": 500_000,  # أقل متوسط فوليوم يومي
    "max_watch": 15,         # أقصى عدد أسهم بيراقبها
    "poll": 60,              # بيبص كل كام ثانية
    # خطة قبل الفتح (كسر سقف البري ماركت)
    "plan_at": "09:25",      # ميعاد رسالة الخطة
    "plan_max": 5,           # أقصى عدد أسهم في الخطة
    "plan_buffer": 0.1,      # الدخول فوق سقف البري ماركت بالنسبة دي %
    "plan_min_risk": 1.0,    # أقل مسافة للستوب %
    "plan_max_risk": 4.0,    # أقصى مسافة للستوب %
    "plan_expire": "10:00",  # لو مكسرش لحد الوقت ده الخطة تتلغي
}

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FALLBACK = ["TSLA", "NVDA", "AMD", "PLTR", "SMCI", "MARA", "COIN", "SOFI", "RIVN", "HOOD"]


# ===================== أدوات =====================
def send(text):
    print(text)
    if not TOKEN or not CHAT_ID:
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": text[:3900]}, timeout=30)
        if not r.ok:
            print("خطأ في الإرسال:", r.text)
    except Exception as e:
        print("خطأ في الإرسال:", e)


def now_ny():
    return dt.datetime.now(NY)


def today_at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return now_ny().replace(hour=h, minute=m, second=0, microsecond=0)


def sleep_until(t):
    while now_ny() < t:
        time.sleep(min(30, max(1, (t - now_ny()).total_seconds())))


def fmt(x):
    return f"{x:.2f}"


# ===================== الأسهم اللي في الملعب =====================
def screen_movers(cfg=CFG):
    found = {}
    for name in ["day_gainers", "small_cap_gainers", "most_actives"]:
        try:
            quotes = yf.screen(name, count=50).get("quotes", [])
        except Exception as e:
            print("تعذر تحميل قايمة", name, e)
            quotes = []
        for q in quotes:
            sym = q.get("symbol")
            price = q.get("regularMarketPrice") or 0
            chg = q.get("regularMarketChangePercent") or 0
            avgv = q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day") or 0
            if not sym or "^" in sym or "=" in sym:
                continue
            if price >= cfg["min_price"] and chg >= cfg["min_change"] and avgv >= cfg["min_avg_vol"]:
                found[sym] = max(chg, found.get(sym, 0))
    ranked = sorted(found, key=lambda s: -found[s])[: cfg["max_watch"]]
    extra = [t.strip().upper() for t in os.environ.get("SCALP_EXTRA", "").split(",") if t.strip()]
    watch = list(dict.fromkeys(ranked + extra))
    return watch, {s: found.get(s, 0.0) for s in watch}


# ===================== البيانات =====================
def pick(data, t):
    if isinstance(data.columns, pd.MultiIndex):
        if t in data.columns.get_level_values(0):
            return data[t]
        if t in data.columns.get_level_values(1):
            return data.xs(t, axis=1, level=1)
        return None
    return data


def get_bars(tickers, period="1d"):
    data = yf.download(tickers, period=period, interval="1m", group_by="ticker",
                       auto_adjust=False, prepost=True, threads=True, progress=False)
    out = {}
    if data is None or len(data) == 0:
        return out
    for t in tickers:
        df = pick(data, t)
        if df is None:
            continue
        df = df.dropna(subset=["Close"])
        if len(df) == 0:
            continue
        df.index = df.index.tz_convert(NY)
        out[t] = df
    return out


def build_5m(df1, now):
    """يحوّل شموع الدقيقة لشموع ٥ دقايق مكتملة بس، ومعاها VWAP"""
    day = now.date()
    df1 = df1[(df1.index.date == day)]
    df1 = df1.between_time("09:30", "15:59")
    if len(df1) == 0:
        return None
    tp = (df1["High"] + df1["Low"] + df1["Close"]) / 3
    vwap1 = (tp * df1["Volume"]).cumsum() / df1["Volume"].cumsum().replace(0, float("nan"))
    rule = dict(rule="5min", origin="start_day", label="left", closed="left")
    bars = df1.resample(**rule).agg({"Open": "first", "High": "max", "Low": "min",
                                    "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    bars["VWAP"] = vwap1.resample(**rule).last()
    last_min = df1.index[-1]
    done = [(s + dt.timedelta(minutes=4) <= last_min) and (s + dt.timedelta(minutes=5) <= now) for s in bars.index]
    return bars[done]


def premarket_high(df1, now):
    pre = df1[(df1.index.date == now.date())].between_time("04:00", "09:29")
    return float(pre["High"].max()) if len(pre) else None


# ===================== البري ماركت =====================
def premarket_scan(cfg=CFG):
    from scanner import universe
    tickers = universe()
    today = now_ny().date()
    ups, downs = [], []
    for i in range(0, len(tickers), 200):
        part = tickers[i:i + 200]
        try:
            frames = get_bars(part, period="2d")
        except Exception as e:
            print("خطأ في بيانات البري ماركت:", e)
            continue
        for t, df in frames.items():
            prev = df[(df.index.date < today)].between_time("09:30", "15:59")
            pre = df[(df.index.date == today)].between_time("04:00", "09:29")
            if len(prev) == 0 or len(pre) == 0:
                continue
            pc = float(prev["Close"].iloc[-1])
            last = float(pre["Close"].iloc[-1])
            vol = float(pre["Volume"].sum())
            if pc <= 0 or last < cfg["min_price"] or vol < cfg["pm_min_vol"]:
                continue
            gap = (last / pc - 1) * 100
            rec = {"t": t, "gap": gap, "price": last, "vol": vol, "pmh": float(pre["High"].max()),
                   "pvwap": premarket_vwap(pre)}
            if gap >= cfg["gap_min"]:
                ups.append(rec)
            elif gap <= -cfg["gap_min"]:
                downs.append(rec)
    ups.sort(key=lambda r: -r["gap"])
    downs.sort(key=lambda r: r["gap"])
    return ups[: cfg["max_watch"]], downs[: cfg["max_avoid"]]


def vol_str(v):
    return f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def premarket_vwap(pre):
    vol = pre["Volume"].sum()
    if vol <= 0:
        return None
    tp = (pre["High"] + pre["Low"] + pre["Close"]) / 3
    return float((tp * pre["Volume"]).sum() / vol)


# ===================== خطة قبل الفتح =====================
def refresh_premarket(recs, now):
    """يحدّث سقف البري ماركت والـ VWAP بتاعه قبل الفتح بدقايق"""
    tickers = [r["t"] for r in recs]
    try:
        frames = get_bars(tickers, period="1d")
    except Exception as e:
        print("تعذر تحديث البري ماركت:", e)
        return recs
    for r in recs:
        df = frames.get(r["t"])
        if df is None:
            continue
        pre = df[(df.index.date == now.date())].between_time("04:00", "09:29")
        if len(pre) == 0:
            continue
        r["pmh"] = float(pre["High"].max())
        r["price"] = float(pre["Close"].iloc[-1])
        r["pvwap"] = premarket_vwap(pre)
    return recs


def make_plan(r, cfg=CFG):
    """دخول فوق سقف البري ماركت، ستوب عند VWAP البري ماركت بين حد أدنى وأقصى، هدف ضعف المخاطرة"""
    pmh = r["pmh"]
    entry = round(pmh * (1 + cfg["plan_buffer"] / 100) + 0.01, 2)
    lo = entry * (1 - cfg["plan_max_risk"] / 100)
    hi = entry * (1 - cfg["plan_min_risk"] / 100)
    pv = r.get("pvwap") or lo
    stop = round(min(max(pv, lo), hi), 2)
    pos = position(entry, stop, cfg)
    if not pos:
        return None
    target, qty = pos
    return {"entry": entry, "stop": stop, "target": round(target, 2), "qty": qty, "state": None, "last": None}


def plan_message(plans, recs):
    lines = ["📋 خطة قبل الفتح — حط الأوامر دي قبل 9:30",
             "أمر Buy Stop عند سعر الدخول (صالح لليوم). لو السهم مكسرش السقف، الأمر مش هيتنفذ.", ""]
    for r in recs:
        p = plans.get(r["t"])
        if not p:
            continue
        lines.append(f"• {r['t']} (+{r['gap']:.1f}%) | سقف البري {fmt(r['pmh'])}")
        lines.append(f"   دخول {fmt(p['entry'])} | ستوب {fmt(p['stop'])} | هدف {fmt(p['target'])} | كمية {p['qty']}")
    lines.append("")
    lines.append(f"لو مكسرش لحد {CFG['plan_expire']} هبعتلك تلغي الأمر. ولو اتنفذ هبلّغك تحط الستوب والهدف.")
    lines.append("⚠️ خطة للمراجعة مش توصية — بص على الشارت قبل ما تحط الأمر.")
    return "\n".join(lines)


def track_plan(t, p, df1, now, cfg=CFG):
    """بيتابع خطة واحدة على شموع الدقيقة المكتملة بعد الفتح"""
    msgs = []
    if p["state"] in ("closed", "cancelled"):
        return msgs
    day = df1[(df1.index.date == now.date())].between_time("09:30", "15:59")
    day = day[[ts + dt.timedelta(minutes=1) <= now for ts in day.index]]
    if p["last"] is not None:
        day = day[day.index > p["last"]]
    h, m = map(int, cfg["plan_expire"].split(":"))
    expire = now.replace(hour=h, minute=m, second=0, microsecond=0)
    for ts, b in day.iterrows():
        p["last"] = ts
        if p["state"] is None:
            if b["High"] >= p["entry"]:
                p["state"] = "open"
                msgs.append(f"⚡ اتنفذ: {t} كسر سقف البري ماركت وعدّى {fmt(p['entry'])} الساعة {ts:%H:%M}\n"
                            f"حط دلوقتي أمر Stop Loss عند {fmt(p['stop'])} وبيع عند الهدف {fmt(p['target'])}")
                continue
            if b["Low"] <= p["stop"]:
                p["state"] = "cancelled"; p["result"] = "اتلغت"
                msgs.append(f"❌ الغي أمر {t}: نزل تحت {fmt(p['stop'])} قبل ما يكسر — الخطة باظت")
                break
            if ts + dt.timedelta(minutes=1) >= expire:
                p["state"] = "cancelled"; p["result"] = "مكسرش"
                msgs.append(f"⌛ الغي أمر {t}: مكسرش {fmt(p['entry'])} لحد {cfg['plan_expire']}")
                break
        elif p["state"] == "open":
            if b["Low"] <= p["stop"]:
                p["state"] = "closed"; p["result"] = "ستوب"
                msgs.append(f"🔴 ستوب خطة: {t} لمس {fmt(p['stop'])} — اخرج لو لسه جوه")
                break
            if b["High"] >= p["target"]:
                p["state"] = "closed"; p["result"] = "هدف"
                msgs.append(f"✅ هدف خطة: {t} وصل {fmt(p['target'])} — خد الربح")
                break
    return msgs


# ===================== المنطق =====================
def position(entry, stop, cfg=CFG):
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + cfg["rr"] * risk
    qty = min((cfg["acct"] * cfg["risk_pct"] / 100) / risk, (cfg["acct"] * cfg["max_pos"] / 100) / entry)
    return target, round(qty, 2)


def evaluate(t, st, bars, cfg=CFG, pmh=None):
    """بيقيّم الشموع الجديدة بس، ويرجّع الرسايل اللي محتاجة تتبعت"""
    msgs = []
    if bars is None or len(bars) < 2:
        return msgs
    first = bars.index[0]
    if (first.hour, first.minute) != (9, 30):
        return msgs
    orH, orL = float(bars["High"].iloc[0]), float(bars["Low"].iloc[0])
    last_entry = first.replace(hour=int(cfg["last_entry"][:2]), minute=int(cfg["last_entry"][3:]))
    start = st.get("next", 1)
    for i in range(start, len(bars)):
        b = bars.iloc[i]
        ts = bars.index[i]
        vw = float(b["VWAP"])
        if st.get("state") == "open":
            if b["Low"] <= st["stop"]:
                msgs.append(f"🔴 ستوب: {t} لمس الستوب {fmt(st['stop'])} — اخرج لو لسه جوه")
                st["state"] = "closed"; st["result"] = "ستوب"
            elif b["High"] >= st["target"]:
                msgs.append(f"✅ هدف: {t} وصل الهدف {fmt(st['target'])} — خد الربح")
                st["state"] = "closed"; st["result"] = "هدف"
            elif b["Close"] < vw:
                msgs.append(f"🟠 اخرج: {t} قفل تحت VWAP عند {fmt(b['Close'])}")
                st["state"] = "closed"; st["result"] = "تحت VWAP"
        elif st.get("state") is None:
            prev_close = float(bars["Close"].iloc[i - 1])
            avg_vol = float(bars["Volume"].iloc[:i].mean())
            bar_end = ts + dt.timedelta(minutes=5)
            if (b["Close"] > orH and prev_close <= orH and b["Close"] > vw
                    and b["Volume"] >= avg_vol * cfg["min_rvol"] and bar_end <= last_entry + dt.timedelta(minutes=5)):
                entry = float(b["Close"])
                stop = vw if orL < vw < entry else orL
                pos = position(entry, stop, cfg)
                if pos:
                    target, qty = pos
                    st.update(state="open", entry=entry, stop=stop, target=target, qty=qty)
                    msgs.append(
                        f"🟢 شراء: {t}\nدخول {fmt(entry)} | ستوب {fmt(stop)} | هدف {fmt(target)} | كمية {qty}\n"
                        f"سقف النطاق {fmt(orH)} | VWAP {fmt(vw)} | الفوليوم {b['Volume'] / avg_vol:.1f}x\n"
                        f"⚠️ لو السعر دلوقتي بعيد عن سعر الدخول بأكتر من نص المسافة للهدف، فوّتها"
                    )
                    if pmh:
                        if entry > pmh:
                            msgs[-1] += f"\n💪 كسر كمان سقف البري ماركت {fmt(pmh)}"
                        elif pmh < target:
                            msgs[-1] += f"\n🧱 سقف البري ماركت {fmt(pmh)} قبل الهدف — مقاومة قريبة، ممكن تاخد ربح جزئي عنده"
    st["next"] = len(bars)
    return msgs


# ===================== التشغيل =====================
def main(force=False):
    n = now_ny()
    if not force:
        hm = n.hour * 100 + n.minute
        if hm < 845 or hm > 935:
            print("مش وقت التشغيل (فرق التوقيت الصيفي/الشتوي) — خروج")
            return

    news = {}

    def add_news(sym):
        if sym not in news:
            mood, dil, top = news_summary(sym, hours=24, max_n=1)
            news[sym] = (mood, dil, top)
        return news[sym]

    pre_watch = []
    ups = []
    if now_ny() < today_at("09:25"):
        sleep_until(today_at("09:12"))
        ups, downs = premarket_scan()
        lines = ["🌅 البري ماركت — الأسهم الفاتحة بفجوة طالعة:"]
        for r in ups:
            mood, dil, top = add_news(r["t"])
            lines.append(f"• {r['t']} +{r['gap']:.1f}% | {fmt(r['price'])} | فوليوم {vol_str(r['vol'])} | سقف البري {fmt(r['pmh'])} {mood}")
            lines.extend(top)
            pre_watch.append(r["t"])
        if not ups:
            lines.append("مفيش فجوات طالعة واضحة النهارده.")
        if downs:
            lines.append("")
            lines.append("⛔ نازلة بفجوة — متشتريش الرخيص فيها:")
            for r in downs:
                mood, dil, top = add_news(r["t"])
                lines.append(f"• {r['t']} {r['gap']:.1f}% | {fmt(r['price'])} {mood}")
                lines.extend(top)
        send("\n".join(lines))

    # خطة قبل الفتح: أقوى الفجوات الطالعة، من غير أسهم فيها خبر طرح أسهم
    plans = {}
    if ups and now_ny() < today_at("09:29"):
        sleep_until(today_at(CFG["plan_at"]))
        cands = [r for r in ups if not news.get(r["t"], ("", False, []))[1]][: CFG["plan_max"]]
        cands = refresh_premarket(cands, now_ny())
        for r in cands:
            p = make_plan(r)
            if p:
                plans[r["t"]] = p
        if plans:
            send(plan_message(plans, cands))

    # المراقبة بتبدأ مع الفتح، والقايمة النهائية بتتضاف الساعة 9:36
    sleep_until(today_at("09:30"))
    watch = list(dict.fromkeys(list(plans) + pre_watch))[:20]
    states = {t: {} for t in watch}
    movers_done = False
    end = today_at(CFG["end"])
    got_data = False
    while now_ny() < end:
        if not movers_done and now_ny() >= today_at("09:36"):
            movers_done = True
            movers, chg = screen_movers()
            watch = list(dict.fromkeys(list(plans) + pre_watch + movers))[:20]
            note = ""
            if not watch:
                watch = FALLBACK
                note = "(قايمة احتياطية — مقدرتش أجيب الأسهم الطالعة)"
            for t in watch:
                states.setdefault(t, {})
            lines = ["🔔 بدأ رصد السكالبنج " + note, "القايمة النهائية:"]
            for s in watch:
                mood, dil, top = add_news(s)
                c = chg.get(s, 0.0)
                lines.append(f"• {s} (+{c:.1f}%) {mood}" if c else f"• {s} {mood}")
            send("\n".join(lines))
        if not watch:
            time.sleep(CFG["poll"])
            continue
        try:
            frames = get_bars(watch)
        except Exception as e:
            print("خطأ في البيانات:", e)
            frames = {}
        if frames:
            got_data = True
        elif not got_data and now_ny() > today_at("09:45"):
            send("📴 مفيش بيانات النهارده — غالبًا السوق أجازة.")
            return
        for t, df in frames.items():
            nn = now_ny()
            if t in plans:
                try:
                    for m in track_plan(t, plans[t], df, nn):
                        send(m)
                except Exception as e:
                    print("تخطي خطة", t, e)
            try:
                for m in evaluate(t, states[t], build_5m(df, nn), pmh=premarket_high(df, nn)):
                    if m.startswith("🟢"):
                        mood, dil, _ = news.get(t, ("", False, []))
                        m += f"\nالأخبار: {mood}"
                        if dil:
                            m += "\n⛔ فيه خبر طرح أسهم — السهم ممكن يقع فجأة، الأفضل تفوّتها"
                    send(m)
            except Exception as e:
                print("تخطي", t, e)
        time.sleep(CFG["poll"])

    lines = ["🏁 البوت وقف الرصد. ملخص النهارده:"]
    any_sig = False
    for t, p in plans.items():
        any_sig = True
        if p["state"] == "open":
            lines.append(f"• خطة {t}: لسه مفتوحة — ستوب {fmt(p['stop'])} | هدف {fmt(p['target'])}. حط الأوامر دي في روبن هود.")
        elif p["state"] is None:
            lines.append(f"• خطة {t}: متنفذتش — الغي الأمر لو لسه موجود")
        elif p["state"] == "cancelled":
            lines.append(f"• خطة {t}: اتلغت ({p.get('result')})")
        else:
            lines.append(f"• خطة {t}: دخول {fmt(p['entry'])} ← {p.get('result')}")
    for t, st in states.items():
        if "entry" not in st:
            continue
        any_sig = True
        if st.get("state") == "open":
            lines.append(f"• {t}: لسه مفتوحة — ستوب {fmt(st['stop'])} | هدف {fmt(st['target'])}. حط الأوامر دي في روبن هود.")
        else:
            lines.append(f"• {t}: دخول {fmt(st['entry'])} ← {st.get('result')}")
    if not any_sig:
        lines.append("مفيش إشارات دخول النهارده.")
    send("\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "test":
        send("✅ بوت السكالبنج شغال ومتصل بتليجرام.")
    else:
        main(force=(mode == "now"))
