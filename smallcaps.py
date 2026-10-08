# الأسهم السمول كاب اللي عليها زخم — الفلترة من ماسح تريدنج فيو
# بالليل بعد الأفتر ماركت: الطالعة والنازلة بخبر، وحركات تستاهل المتابعة، وأقوى سهم لبكرة
# الصبح قبل 7: الأسهم الفاتحة بفجوة في البري ماركت ومعاها مستويات الدخول والستوب والهدف
# لو تريدنج فيو مش متاح لأي سبب، البوت بيرجع لبيانات ياهو

import datetime as dt
import math
import os
import re
import sys

import pandas as pd
import requests
import yfinance as yf

from news import news_summary, earnings_note
from scalper import get_bars, make_plan, premarket_vwap, vol_str, fmt, send, now_ny, today_at, sleep_until

CFG = {
    # فلتر السمول كاب
    "min_mcap": 30e6,          # أقل قيمة سوقية $
    "max_mcap": 2e9,           # أقصى قيمة سوقية $
    "min_price": 1.0,          # أقل سعر
    "min_avg_vol": 200_000,    # أقل متوسط فوليوم يومي (لقايمة بوت السكالبنج)
    # بالليل
    "ah_min_chg": 5.0,         # أقل طلوع/نزول بعد الإغلاق %
    "ah_min_vol": 50_000,      # أقل فوليوم بعد الإغلاق
    "ah_big_vol": 1_000_000,   # فوليوم عالي بعد الإغلاق حتى لو الحركة صغيرة
    "ah_big_min_chg": 2.0,     # بشرط الحركة تكون النسبة دي % على الأقل
    "ah_big_ratio": 0.5,       # وفوليوم بعد الإغلاق نص متوسطه اليومي على الأقل
    "day_big_chg": 20.0,       # سهم طلع النهارده بالنسبة دي % أو أكتر
    "day_big_vol": 2_000_000,  # بفوليوم كده أو أكتر
    "weak_vol": 300_000,       # أقل من كده الفجوة ممكن تتمسح الصبح
    "max_up": 8, "max_down": 5, "max_other": 5,
    # الصبح
    "pm_min_gap": 5.0,         # أقل فجوة في البري ماركت %
    "pm_min_vol": 20_000,      # أقل فوليوم في البري ماركت لحد وقت الفحص
    "max_list": 10,
    "morning_at": "06:45",     # ميعاد رسالة الصبح
    "night_after": "20:01",    # فحص الليل يبدأ بعد الأفتر ماركت ما يقفل
    # احتياطي ياهو
    "yahoo_max_price": 20.0,
    "ah_min_vol_yahoo": 30_000,
}

UNIVERSE_FILE = "small_universe.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (scanner)"}
SKIP_NAME = re.compile(r"\b(warrants?|units?|rights?|preferred|notes|debentures?|acquisition corp\.?|acquisition co\.?)\b")
TV_URL = "https://scanner.tradingview.com/america/scan"
TV_COLS = ["name", "description", "close", "change", "volume", "postmarket_close", "postmarket_change",
           "postmarket_volume", "postmarket_high", "premarket_close", "premarket_change", "premarket_volume",
           "premarket_high", "market_cap_basic", "average_volume_10d_calc"]


# ===================== تريدنج فيو =====================
def tv_scan(filters, sort_by=None, asc=False, n=50, cols=TV_COLS):
    """ماسح تريدنج فيو: بيرجّع قايمة أسهم كل واحد dict بالأعمدة"""
    base = [{"left": "type", "operation": "equal", "right": "stock"},
            {"left": "market_cap_basic", "operation": "in_range", "right": [CFG["min_mcap"], CFG["max_mcap"]]},
            {"left": "close", "operation": "greater", "right": CFG["min_price"]}]
    body = {"markets": ["america"], "columns": cols, "filter": base + filters, "range": [0, n]}
    if sort_by:
        body["sort"] = {"sortBy": sort_by, "sortOrder": "asc" if asc else "desc"}
    r = requests.post(TV_URL, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for row in r.json().get("data") or []:
        d = dict(zip(cols, row["d"]))
        t = str(d.get("name") or "").replace(".", "-")
        if not re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t) or t[-2:] in ("-U", "-W", "-R"):
            continue
        d["t"] = t
        d["sym"] = row.get("s")
        out.append(d)
    return out


def num(x):
    return float(x) if isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x)) else 0.0


def tv_universe():
    rows = tv_scan([{"left": "average_volume_10d_calc", "operation": "greater", "right": CFG["min_avg_vol"]}],
                   n=6000, cols=["name"])
    return sorted({r["t"] for r in rows})


def build_universe(cfg=CFG):
    """قايمة السمول كاب لبوت السكالبنج: من تريدنج فيو، ولو فشل من ياهو"""
    try:
        keep = tv_universe()
        print("قايمة السمول كاب من تريدنج فيو:", len(keep))
    except Exception as e:
        print("تريدنج فيو مش متاح، هستخدم ياهو:", e)
        return build_universe_yahoo(cfg)
    if keep:
        with open(UNIVERSE_FILE, "w") as f:
            f.write("\n".join(keep) + "\n")
    return keep


def add_news(r, max_n=1):
    r["mood"], r["dil"], r["top"] = news_summary(r["t"], hours=24, max_n=max_n, sym=r.get("sym"))
    r["has_news"] = bool(r["top"])
    note = earnings_note(r["t"], warn_days=1)
    r["earn"] = note if "⚠️" in note else ""
    return r


def tv_night(cfg=CFG):
    """أرقام الأفتر ماركت النهائية من تريدنج فيو"""
    vol = {"left": "postmarket_volume", "operation": "greater", "right": cfg["ah_min_vol"]}
    ups = tv_scan([vol, {"left": "postmarket_change", "operation": "greater", "right": cfg["ah_min_chg"]}],
                  "postmarket_change", n=cfg["max_up"])
    downs = tv_scan([vol, {"left": "postmarket_change", "operation": "less", "right": -cfg["ah_min_chg"]}],
                    "postmarket_change", asc=True, n=cfg["max_down"])
    seen = {r["t"] for r in ups + downs}
    big_ah = [r for r in tv_scan([{"left": "postmarket_volume", "operation": "greater", "right": cfg["ah_big_vol"]}],
                                 "postmarket_volume", n=30)
              if abs(num(r.get("postmarket_change"))) >= cfg["ah_big_min_chg"]
              and num(r.get("postmarket_volume")) >= cfg["ah_big_ratio"] * num(r.get("average_volume_10d_calc"))]
    big_day = tv_scan([{"left": "change", "operation": "greater", "right": cfg["day_big_chg"]},
                       {"left": "volume", "operation": "greater", "right": cfg["day_big_vol"]}], "change", n=10)
    others = []
    for r in big_ah + big_day:
        if r["t"] not in seen and len(others) < cfg["max_other"]:
            seen.add(r["t"])
            others.append(r)
    for r in ups + downs + others:
        add_news(r)
    return ups, downs, others


def best_pick(ups, cfg=CFG):
    """أقوى سهم لبكرة: خبر إيجابي + فوليوم كبير + طلوع، ومن غير طرح أسهم"""
    best, best_s = None, 0
    for r in ups:
        v = num(r.get("postmarket_volume"))
        if r["dil"] or v < cfg["weak_vol"]:
            continue
        w = 1.5 if r["mood"].startswith("🟢") else (0.5 if r["mood"].startswith("🔴") else 1.0)
        s = min(num(r.get("postmarket_change")), 50) * math.log10(max(v, 10)) * w
        if s > best_s:
            best, best_s = r, s
    return best


def tv_morning(cfg=CFG):
    rows = tv_scan([{"left": "premarket_change", "operation": "greater", "right": cfg["pm_min_gap"]},
                    {"left": "premarket_volume", "operation": "greater", "right": cfg["pm_min_vol"]}],
                   "premarket_change", n=cfg["max_list"])
    out = []
    try:
        frames = get_bars([r["t"] for r in rows], period="1d") if rows else {}
    except Exception as e:
        print("تعذر تحميل شموع البري ماركت:", e)
        frames = {}
    for r in rows:
        rec = {"t": r["t"], "sym": r.get("sym"), "name": r.get("description") or "", "gap": num(r["premarket_change"]),
               "price": num(r["premarket_close"]), "vol": num(r["premarket_volume"]),
               "pmh": num(r["premarket_high"]) or num(r["premarket_close"]),
               "ah": num(r.get("postmarket_change")), "mcap": num(r.get("market_cap_basic")), "pvwap": None}
        df = frames.get(r["t"])
        if df is not None:
            pre = df[df.index.date == now_ny().date()].between_time("04:00", "09:29")
            if len(pre):
                rec["pvwap"] = premarket_vwap(pre)
                rec["pmh"] = max(rec["pmh"], float(pre["High"].max()))
        out.append(add_news(rec))
    return out


# ===================== احتياطي: بيانات ياهو =====================
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


def build_universe_yahoo(cfg=CFG):
    """احتياطي: بيصفّي الأسهم بالسعر والفوليوم من ياهو ويحفظ القايمة في ملف"""
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
                if cfg["min_price"] <= last <= cfg["yahoo_max_price"] and avgv >= cfg["min_avg_vol"]:
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
            if close <= 0 or last < cfg["min_price"] or vol < cfg["ah_min_vol_yahoo"]:
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
NAME_SUFFIX = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd", "ltd.", "n.v.", "plc", "s.a.",
               "holdings", "group", "class", "a", "b", "common", "stock", "shares", "ordinary", "the"}


def short_name(r):
    """اسم الشركة من غير Inc و Corp وكده، ومقصوص على كلمات كاملة"""
    n = (r.get("description") or "").split(",")[0].split(" - ")[0]
    words = n.split()
    while words and words[-1].lower() in NAME_SUFFIX:
        words.pop()
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > 24:
            break
        out = f"{out} {w}".strip()
    return f" ({out})" if out and out != r["t"] else ""


def news_line(r):
    if r["dil"]:
        return r["top"] + ["   ⛔ خبر طرح أسهم — السهم ممكن يقع فجأة"]
    return r["top"] if r["top"] else ["   ⚪ مفيش خبر واضح"]


def night_message(ups, downs, others, day):
    lines = [f"🌙 الأفتر ماركت — {day}",
             "أسهم سمول كاب (قيمة سوقية من 30 مليون لـ 2 مليار) من ماسح تريدنج فيو.",
             "الأفتر ماركت قفل الساعة 8، فدي الأرقام النهائية.", ""]
    lines.append("🟢 طالعة بعد الإغلاق")
    if not ups:
        lines.append("مفيش أسهم طلعت بقوة بعد الإغلاق النهارده.")
    for r in ups:
        lines.append(f"• {r['t']}{short_name(r)}: قفل {fmt(num(r['close']))} → +{num(r['postmarket_change']):.1f}% "
                     f"({fmt(num(r['postmarket_close']))}) | فوليوم {vol_str(num(r['postmarket_volume']))}")
        lines.extend(news_line(r))
        if r["earn"]:
            lines.append(r["earn"])
    lines.append("")
    lines.append("🔴 نازلة بعد الإغلاق")
    if not downs:
        lines.append("مفيش أسهم نزلت بقوة بعد الإغلاق.")
    for r in downs:
        lines.append(f"• {r['t']}{short_name(r)}: قفل {fmt(num(r['close']))} → {num(r['postmarket_change']):.1f}% "
                     f"| فوليوم {vol_str(num(r['postmarket_volume']))}")
        lines.extend(news_line(r))
    if others:
        lines.append("")
        lines.append("👀 حركات تانية تستاهل المتابعة")
        for r in others:
            ah = num(r.get("postmarket_change"))
            ahv = num(r.get("postmarket_volume"))
            day_chg = num(r.get("change"))
            if day_chg >= CFG["day_big_chg"]:
                lines.append(f"• {r['t']}{short_name(r)}: طلع {day_chg:+.1f}% النهارده بفوليوم {vol_str(num(r['volume']))}، "
                             f"وبعد الإغلاق {ah:+.1f}%")
            else:
                avg = num(r.get("average_volume_10d_calc"))
                ratio = f" ({ahv / avg:.1f} ضعف متوسطه اليومي)" if avg else ""
                lines.append(f"• {r['t']}{short_name(r)}: {ah:+.1f}% بعد الإغلاق بفوليوم {vol_str(ahv)}{ratio}")
            lines.append(r["top"][0] if r["top"] else "   ⚪ مفيش خبر واضح — ممكن حركة قطاع أو مضاربة")
    best = best_pick(ups)
    lines.append("")
    if best:
        lines.append(f"⭐ أقوى سهم بكرة: {best['t']}")
        if best["mood"].startswith("🟢"):
            lines.append("• عليه خبر إيجابي")
        elif best["has_news"]:
            lines.append("• عليه خبر")
        lines.append(f"• فوليوم كبير بعد الإغلاق ({vol_str(num(best['postmarket_volume']))})، يعني فيه اهتمام حقيقي")
    else:
        lines.append("⭐ مفيش سهم واضح لبكرة — مفيش طلوع بخبر وفوليوم كفاية.")
    weak = [r for r in ups if r is not best and num(r.get("postmarket_volume")) < CFG["weak_vol"]]
    if weak:
        lines.append("• فوليوم ضعيف، الفجوة ممكن تتمسح الصبح: " + "، ".join(r["t"] for r in weak))
    lines += ["",
              "📌 بكرة بالـ ORB + VWAP:",
              "متدخلش أول 5 دقايق بعد 9:30، وادخل لو كسر أعلى النطاق وهو فوق الـ VWAP.",
              "الستوب تحت أوطى النطاق أو تحت الـ VWAP. ولو فتح بفجوة ونزل تحت الـ VWAP، الفجوة بتتمسح ومتدخلش.",
              "⚠️ أسعار بعد الإغلاق مش مضمونة والسهم ممكن يفتح بسعر مختلف. السمول كابس متذبذبة جدًا:",
              "استخدم Limit Order وحجم صغير. للمراجعة مش توصية."]
    return "\n".join(lines)


def morning_message(recs, now, src="تريدنج فيو"):
    lines = [f"☀️ البري ماركت — سمول كاب فاتحة بفجوة ({now:%H:%M})",
             f"المصدر: {src}" + (" (بيانات متأخرة حوالي 15 دقيقة)" if src == "تريدنج فيو" else ""), ""]
    if not recs:
        lines.append("مفيش أسهم سمول كاب فاتحة بفجوة واضحة لحد دلوقتي.")
        return "\n".join(lines)
    for r in recs:
        tag = f" | 🌙 كان {r['ah']:+.0f}% امبارح بعد الإغلاق" if abs(r["ah"]) >= CFG["ah_min_chg"] else ""
        mc = f" | قيمة سوقية {mcap_str(r['mcap'])}" if r.get("mcap") else ""
        lines.append(f"• {r['t']} +{r['gap']:.1f}% | {fmt(r['price'])} | فوليوم {vol_str(r['vol'])}{mc}{tag}")
        lines.extend(news_line(r))
        p = None if r["dil"] else make_plan(r)
        if p:
            lines.append(f"   كسر سقف البري {fmt(r['pmh'])}: دخول {fmt(p['entry'])} | ستوب {fmt(p['stop'])} | "
                         f"هدف {fmt(p['target'])} | كمية {p['qty']}")
    lines.append("")
    lines.append("📌 في البري ماركت روبن هود بيقبل أوامر Limit بس، والستوب مش بيشتغل غير بعد 9:30.")
    lines.append("يعني لو دخلت قبل الفتح لازم تراقب الستوب بنفسك.")
    lines.append("⚠️ السمول كابس حركتها عنيفة وممكن تقف فجأة — للمراجعة مش توصية.")
    return "\n".join(lines)


def night_message_yahoo(recs, day):
    lines = [f"🌙 أسهم صغيرة عليها زخم بعد الإقفال — {day}", "(تريدنج فيو مكانش متاح، دي بيانات ياهو)", ""]
    if not recs:
        lines.append("مفيش أسهم صغيرة طلعت بقوة بعد الإقفال النهارده.")
    for r in recs:
        lines.append(f"• {r['t']} +{r['chg']:.1f}% | آخر سعر {fmt(r['last'])} | فوليوم {vol_str(r['vol'])} {r['mood']}")
        lines.extend(r["top"])
    lines.append("⚠️ للمراجعة مش توصية.")
    return "\n".join(lines)


# ===================== التشغيل =====================
def night(force=False):
    n = now_ny()
    if not force and (n.weekday() > 4 or n.hour != 20):
        print("مش وقت فحص الليل — خروج")
        return
    sleep_until(today_at(CFG["night_after"]))
    tickers = build_universe()
    try:
        ups, downs, others = tv_night()
        send(night_message(ups, downs, others, now_ny().date()))
    except Exception as e:
        print("تريدنج فيو مش متاح، هستخدم ياهو:", e)
        recs = enrich(scan_after_hours(tickers, now_ny()))
        send(night_message_yahoo(recs, now_ny().date()))


def morning(force=False):
    n = now_ny()
    if not force and (n.weekday() > 4 or not (6 * 60 + 15 <= n.hour * 60 + n.minute <= 7 * 60 + 15)):
        print("مش وقت فحص الصبح — خروج")
        return
    sleep_until(today_at(CFG["morning_at"]))
    try:
        recs = tv_morning()
        send(morning_message(recs, now_ny()))
    except Exception as e:
        print("تريدنج فيو مش متاح، هستخدم ياهو:", e)
        recs = enrich(scan_premarket(load_universe(), now_ny()))
        send(morning_message(recs, now_ny(), src="ياهو"))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "test":
        send("✅ بوت السمول كاب شغال ومتصل بتليجرام.")
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
