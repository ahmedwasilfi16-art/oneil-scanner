# الأسهم الصغيرة اللي عليها زخم
# بالليل بعد الأفتر ماركت: الأسهم اللي طلعت بعد الإقفال ومعاها الأخبار
# الصبح قبل 7: الأسهم الفاتحة بفجوة في البري ماركت ومعاها مستويات الدخول والستوب والهدف

import datetime as dt
import os
import re
import sys

import pandas as pd
import requests
import yfinance as yf

from news import news_summary, earnings_note
from scalper import get_bars, make_plan, premarket_vwap, vol_str, fmt, send, now_ny, today_at, sleep_until

CFG = {
    "min_price": 1.0,          # أقل سعر
    "max_price": 20.0,         # أعلى سعر
    "min_avg_vol": 200_000,    # أقل متوسط فوليوم يومي
    "max_mcap": 2e9,           # أقصى قيمة سوقية $
    "ah_min_chg": 5.0,         # أقل طلوع بعد الإقفال %
    "ah_min_vol": 30_000,      # أقل فوليوم في الأفتر ماركت
    "pm_min_gap": 5.0,         # أقل فجوة في البري ماركت %
    "pm_min_vol": 20_000,      # أقل فوليوم في البري ماركت لحد وقت الفحص
    "max_list": 15,            # أقصى عدد أسهم في الرسالة
    "morning_at": "06:45",     # ميعاد رسالة الصبح
    "night_after": "20:01",    # فحص الليل يبدأ بعد الأفتر ماركت ما يقفل
}

UNIVERSE_FILE = "small_universe.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (scanner)"}
SKIP_NAME = re.compile(r"\b(warrants?|units?|rights?|preferred|notes|debentures?|acquisition corp\.?|acquisition co\.?)\b")


# ===================== قايمة الأسهم =====================
def listed_symbols():
    """كل الأسهم المسجلة في البورصات الأمريكية من قوايم Nasdaq الرسمية"""
    out = set()
    srcs = [("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol", "Test Issue"),
            ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol", "Test Issue")]
    for url, col, test_col in srcs:
        try:
            text = requests.get(url, headers=HEADERS, timeout=60).text
        except Exception as e:
            print("تعذر تحميل", url, e)
            continue
        lines = [l.strip("\r") for l in text.split("\n") if l.strip() and not l.startswith("File Creation")]
        head = lines[0].split("|")
        for l in lines[1:]:
            row = dict(zip(head, l.split("|")))
            sym = row.get(col, "")
            name = row.get("Security Name", "").lower()
            if row.get(test_col) == "Y" or row.get("ETF") == "Y":
                continue
            if not re.fullmatch(r"[A-Z]{1,5}", sym):
                continue
            if SKIP_NAME.search(name):
                continue
            out.add(sym)
    return sorted(out)


def build_universe(cfg=CFG):
    """بيصفّي الأسهم بالسعر والفوليوم ويحفظ القايمة في ملف"""
    syms = listed_symbols()
    print("عدد الأسهم المسجلة:", len(syms))
    keep = []
    for i in range(0, len(syms), 400):
        part = syms[i:i + 400]
        try:
            data = yf.download(part, period="1mo", interval="1d", group_by="ticker",
                               auto_adjust=False, threads=True, progress=False)
        except Exception as e:
            print("خطأ في البيانات اليومية:", e)
            continue
        for t in part:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                df = df.dropna(subset=["Close"])
                if len(df) < 5:
                    continue
                last = float(df["Close"].iloc[-1])
                avgv = float(df["Volume"].tail(20).mean())
                if cfg["min_price"] <= last <= cfg["max_price"] and avgv >= cfg["min_avg_vol"]:
                    keep.append(t)
            except Exception:
                pass
    print("عدد الأسهم الصغيرة بعد التصفية:", len(keep))
    if keep:
        with open(UNIVERSE_FILE, "w") as f:
            f.write("\n".join(keep) + "\n")
    return keep


def load_universe():
    if os.path.exists(UNIVERSE_FILE):
        with open(UNIVERSE_FILE) as f:
            syms = [l.strip() for l in f if l.strip()]
        if syms:
            return syms
    return build_universe()


def market_cap(t):
    try:
        return float(yf.Ticker(t).fast_info["marketCap"] or 0)
    except Exception:
        return 0.0


def mcap_str(v):
    if not v:
        return "؟"
    return f"{v / 1e9:.1f}B" if v >= 1e9 else f"{v / 1e6:.0f}M"


# ===================== التحليل =====================
def session_parts(df, day):
    d = df[df.index.date == day]
    return (d.between_time("04:00", "09:29"), d.between_time("09:30", "15:59"), d.between_time("16:00", "19:59"))


def scan_after_hours(tickers, now, cfg=CFG):
    """الأسهم اللي طلعت بعد إقفال النهارده"""
    out = []
    for i in range(0, len(tickers), 200):
        part = tickers[i:i + 200]
        try:
            frames = get_bars(part, period="1d")
        except Exception as e:
            print("خطأ في بيانات الأفتر ماركت:", e)
            continue
        for t, df in frames.items():
            _, reg, post = session_parts(df, now.date())
            if len(reg) == 0 or len(post) == 0:
                continue
            close = float(reg["Close"].iloc[-1])
            last = float(post["Close"].iloc[-1])
            vol = float(post["Volume"].sum())
            if close <= 0 or last < cfg["min_price"] or vol < cfg["ah_min_vol"]:
                continue
            chg = (last / close - 1) * 100
            if chg >= cfg["ah_min_chg"]:
                out.append({"t": t, "chg": chg, "close": close, "last": last,
                            "high": float(post["High"].max()), "vol": vol})
    out.sort(key=lambda r: -r["chg"])
    return out


def scan_premarket(tickers, now, cfg=CFG):
    """الأسهم الفاتحة بفجوة في البري ماركت لحد دلوقتي، ومعاها حركة امبارح بعد الإقفال"""
    out = []
    for i in range(0, len(tickers), 200):
        part = tickers[i:i + 200]
        try:
            frames = get_bars(part, period="2d")
        except Exception as e:
            print("خطأ في بيانات البري ماركت:", e)
            continue
        for t, df in frames.items():
            pre, _, _ = session_parts(df, now.date())
            prev_days = sorted({d for d in df.index.date if d < now.date()})
            if len(pre) == 0 or not prev_days:
                continue
            _, reg_y, post_y = session_parts(df, prev_days[-1])
            if len(reg_y) == 0:
                continue
            close = float(reg_y["Close"].iloc[-1])
            last = float(pre["Close"].iloc[-1])
            vol = float(pre["Volume"].sum())
            if close <= 0 or last < cfg["min_price"] or vol < cfg["pm_min_vol"]:
                continue
            gap = (last / close - 1) * 100
            if gap < cfg["pm_min_gap"]:
                continue
            ah = (float(post_y["Close"].iloc[-1]) / close - 1) * 100 if len(post_y) else 0.0
            out.append({"t": t, "gap": gap, "price": last, "vol": vol, "pmh": float(pre["High"].max()),
                        "pvwap": premarket_vwap(pre), "ah": ah})
    out.sort(key=lambda r: -r["gap"])
    return out


def enrich(recs, cfg=CFG):
    """القيمة السوقية والأخبار لأول الأسهم بس، وبيشيل الكبيرة"""
    res = []
    for r in recs:
        if len(res) >= cfg["max_list"]:
            break
        mc = market_cap(r["t"])
        if mc and mc > cfg["max_mcap"]:
            continue
        r["mcap"] = mc
        r["mood"], r["dil"], r["top"] = news_summary(r["t"], hours=24, max_n=1)
        note = earnings_note(r["t"], warn_days=1)
        r["earn"] = note if "⚠️" in note else ""
        res.append(r)
    return res


# ===================== الرسايل =====================
def night_message(recs, day):
    lines = [f"🌙 أسهم صغيرة عليها زخم بعد الإقفال — {day}", ""]
    if not recs:
        lines.append("مفيش أسهم صغيرة طلعت بقوة بعد الإقفال النهارده.")
        return "\n".join(lines)
    for r in recs:
        lines.append(f"• {r['t']} +{r['chg']:.1f}% | آخر سعر {fmt(r['last'])} | أعلى {fmt(r['high'])} | "
                     f"فوليوم {vol_str(r['vol'])} | قيمة سوقية {mcap_str(r['mcap'])} {r['mood']}")
        lines.extend(r["top"])
        if r["earn"]:
            lines.append(r["earn"])
    lines.append("")
    lines.append("بكرة الصبح قبل 7 هبعتلك مين فيهم لسه ماسك في البري ماركت، ومعاه المستويات.")
    lines.append("⚠️ الأسهم الصغيرة حركتها عنيفة وممكن تقف فجأة — للمراجعة مش توصية.")
    return "\n".join(lines)


def morning_message(recs, now):
    lines = [f"☀️ البري ماركت — أسهم صغيرة فاتحة بفجوة ({now:%H:%M})", ""]
    if not recs:
        lines.append("مفيش أسهم صغيرة فاتحة بفجوة واضحة لحد دلوقتي.")
        return "\n".join(lines)
    for r in recs:
        tag = f" | 🌙 كان طالع +{r['ah']:.0f}% امبارح بعد الإقفال" if r["ah"] >= CFG["ah_min_chg"] else ""
        lines.append(f"• {r['t']} +{r['gap']:.1f}% | {fmt(r['price'])} | فوليوم {vol_str(r['vol'])} | "
                     f"قيمة سوقية {mcap_str(r['mcap'])}{tag} {r['mood']}")
        lines.extend(r["top"])
        p = None if r["dil"] else make_plan(r)
        if p:
            lines.append(f"   كسر سقف البري {fmt(r['pmh'])}: دخول {fmt(p['entry'])} | ستوب {fmt(p['stop'])} | "
                         f"هدف {fmt(p['target'])} | كمية {p['qty']}")
        elif r["dil"]:
            lines.append("   ⛔ عليه خبر طرح أسهم — الأفضل تفوّته")
    lines.append("")
    lines.append("📌 في البري ماركت روبن هود بيقبل أوامر Limit بس، والستوب مش بيشتغل غير بعد 9:30.")
    lines.append("يعني لو دخلت قبل الفتح لازم تراقب الستوب بنفسك.")
    lines.append("⚠️ الأسهم الصغيرة حركتها عنيفة وممكن تقف فجأة — للمراجعة مش توصية.")
    return "\n".join(lines)


# ===================== التشغيل =====================
def night(force=False):
    n = now_ny()
    if not force and (n.weekday() > 4 or n.hour != 20):
        print("مش وقت فحص الليل — خروج")
        return
    sleep_until(today_at(CFG["night_after"]))
    tickers = build_universe()
    recs = enrich(scan_after_hours(tickers, now_ny()))
    send(night_message(recs, now_ny().date()))


def morning(force=False):
    n = now_ny()
    if not force and (n.weekday() > 4 or not (6 * 60 + 15 <= n.hour * 60 + n.minute <= 7 * 60 + 15)):
        print("مش وقت فحص الصبح — خروج")
        return
    tickers = load_universe()
    sleep_until(today_at(CFG["morning_at"]))
    recs = enrich(scan_premarket(tickers, now_ny()))
    send(morning_message(recs, now_ny()))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "test":
        send("✅ بوت الأسهم الصغيرة شغال ومتصل بتليجرام.")
    elif mode in ("night", "night_now"):
        night(force=(mode == "night_now"))
    elif mode in ("morning", "morning_now"):
        morning(force=(mode == "morning_now"))
    else:
        # التشغيل المجدول: يحدد لوحده ليل ولا صبح حسب الساعة في نيويورك
        h = now_ny().hour
        if h >= 18:
            night()
        else:
            morning()
