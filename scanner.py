# سكانر سلم أونيل - بيفحص السوق بعد الإقفال ويبعت الإشارات على تليجرام
# نفس قواعد مؤشر "سلم أونيل - سوينج" على تريدينج فيو

import io
import os
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ===================== الإعدادات =====================
CFG = {
    "base_len": 35,          # طول القاعدة بالأيام
    "max_depth": 35.0,       # أقصى عمق للقاعدة %
    "vol_mult": 1.4,         # مضاعف الفوليوم عند الاختراق
    "buy_zone": 5.0,         # أقصى بعد عن نقطة الشراء %
    "ready_pct": 3.0,        # "جاهز" لما يقرب من النقطة بالنسبة دي %
    "stop_pct": 7.0,         # الستوب %
    "tp_pct": 20.0,          # الهدف %
    "acct": 6000.0,          # رأس المال $
    "risk_pct": 1.0,         # مخاطرة كل صفقة %
    "max_pos": 25.0,         # أقصى حجم للصفقة % من رأس المال
    "rs_len": 126,           # فترة القوة النسبية بالأيام
    "min_price": 10.0,       # أقل سعر للسهم
    "min_dollar_vol": 5_000_000,  # أقل متوسط سيولة يومية بالدولار
    "max_ready": 15,         # أقصى عدد أسهم "جاهز" في الرسالة
}

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (scanner)"}


# ===================== قايمة الأسهم =====================
def wiki_tickers(url, cols):
    html = requests.get(url, headers=HEADERS, timeout=30).text
    for table in pd.read_html(io.StringIO(html)):
        for col in cols:
            if col in table.columns:
                return table[col].astype(str).str.strip().tolist()
    return []


def universe():
    sources = [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["Symbol"]),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ["Symbol", "Ticker symbol"]),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", ["Symbol", "Ticker symbol"]),
        ("https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"]),
    ]
    tickers = set()
    for url, cols in sources:
        try:
            tickers.update(wiki_tickers(url, cols))
        except Exception as e:
            print("تعذر تحميل القايمة:", url, e)
    if not tickers:
        # قايمة احتياطية لو ويكيبيديا مش متاحة
        tickers.update("AAPL MSFT NVDA AMZN META GOOGL TSLA AMD AVGO NFLX CRM ADBE NOW PANW CRWD ANET UBER SHOP PLTR SMCI LLY COST ORCL MU QCOM INTU ISRG AXON DECK CELH".split())
    extra = os.environ.get("EXTRA_TICKERS", "")
    tickers.update(t.strip().upper() for t in extra.split(",") if t.strip())
    return sorted(t.replace(".", "-") for t in tickers if t and t.lower() != "nan")


# ===================== البيانات =====================
def download(tickers, chunk=200):
    frames = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        data = yf.download(part, period="2y", interval="1d", group_by="ticker",
                           auto_adjust=True, threads=True, progress=False)
        for t in part:
            try:
                df = data[t] if len(part) > 1 else data
                df = df.dropna()
                if len(df) > 0:
                    frames[t] = df
            except Exception:
                pass
    return frames


# ===================== التحليل =====================
def analyze(df, spy_close, cfg=CFG):
    df = df.dropna()
    if len(df) < 260:
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    s = spy_close.reindex(df.index).ffill()

    sma50 = c.rolling(50).mean()
    sma150 = c.rolling(150).mean()
    sma200 = c.rolling(200).mean()
    low52 = l.rolling(252).min()
    high52 = h.rolling(252).max()
    trend = (c > sma50) & (sma50 > sma150) & (sma150 > sma200) & (c >= low52 * 1.3) & (c >= high52 * 0.75)

    n = cfg["rs_len"]
    rs_line = c / s
    rs_ok = (c / c.shift(n) > s / s.shift(n)) & (rs_line > rs_line.rolling(50).mean())

    pivot = h.rolling(cfg["base_len"]).max().shift(1)
    base_low = l.rolling(cfg["base_len"]).min().shift(1)
    depth = (pivot - base_low) / pivot * 100
    avg_vol = v.rolling(50).mean()

    setup = trend & rs_ok & (depth <= cfg["max_depth"])
    cross = (c > pivot) & (c.shift(1) <= pivot.shift(1))
    in_zone = c <= pivot * (1 + cfg["buy_zone"] / 100)
    vol_ok = v >= avg_vol.shift(1) * cfg["vol_mult"]
    buy = setup & cross & in_zone & vol_ok
    near = setup & (c < pivot) & (c >= pivot * (1 - cfg["ready_pct"] / 100))
    ready = near & ~near.shift(1, fill_value=False).astype(bool)

    last = -1
    price = float(c.iloc[last])
    dollar_vol = float((c * v).rolling(50).mean().iloc[last])
    if price < cfg["min_price"] or not np.isfinite(dollar_vol) or dollar_vol < cfg["min_dollar_vol"]:
        return None

    info = {
        "price": price,
        "pivot": float(pivot.iloc[last]),
        "depth": float(depth.iloc[last]),
        "vol_ratio": float(v.iloc[last] / avg_vol.iloc[last - 1]) if avg_vol.iloc[last - 1] else 0.0,
        "date": df.index[last].strftime("%Y-%m-%d"),
    }
    if bool(buy.iloc[last]):
        info["type"] = "buy"
    elif bool(ready.iloc[last]):
        info["type"] = "ready"
    else:
        return None
    return info


def position(entry, cfg=CFG):
    stop = entry * (1 - cfg["stop_pct"] / 100)
    target = entry * (1 + cfg["tp_pct"] / 100)
    risk_shares = (cfg["acct"] * cfg["risk_pct"] / 100) / (entry - stop)
    cap_shares = (cfg["acct"] * cfg["max_pos"] / 100) / entry
    qty = round(min(risk_shares, cap_shares), 2)
    return stop, target, qty


# ===================== تليجرام =====================
def send(text):
    if not TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for i in range(0, len(text), 3900):
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text[i:i + 3900]}, timeout=30)
        if not r.ok:
            print("خطأ في الإرسال:", r.text)


def market_ok(spy):
    c = spy["Close"]
    return bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1] and c.iloc[-1] > c.rolling(200).mean().iloc[-1])


# ===================== التشغيل =====================
def main():
    spy = yf.download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy = spy.dropna()
    day = spy.index[-1].strftime("%Y-%m-%d")

    if not market_ok(spy):
        send(f"📉 تقرير {day}\nالسوق هابط (SPY تحت متوسط ٥٠ أو ٢٠٠)\nمفيش إشارات شراء النهارده — استنى.")
        return

    tickers = universe()
    print("عدد الأسهم:", len(tickers))
    frames = download(tickers)
    buys, readies = [], []
    for t, df in frames.items():
        try:
            res = analyze(df, spy["Close"])
        except Exception as e:
            print("تخطي", t, e)
            continue
        if res is None or res["date"] != day:
            continue
        res["ticker"] = t
        (buys if res["type"] == "buy" else readies).append(res)

    buys.sort(key=lambda x: -x["vol_ratio"])
    readies.sort(key=lambda x: (x["pivot"] - x["price"]) / x["price"])

    lines = [f"📊 تقرير سلم أونيل — {day}", f"السوق صاعد ✅ | اتفحص {len(frames)} سهم", ""]
    if buys:
        lines.append("🟢 إشارات شراء (اختراق بفوليوم):")
        for b in buys:
            stop, target, qty = position(b["price"])
            lines.append(
                f"• {b['ticker']}: دخول {b['price']:.2f} | ستوب {stop:.2f} | هدف {target:.2f} | "
                f"كمية {qty} | الفوليوم {b['vol_ratio']:.1f}x | عمق القاعدة {b['depth']:.0f}%"
            )
        lines.append("")
    else:
        lines.append("🟢 مفيش إشارات شراء النهارده.")
        lines.append("")

    if readies:
        lines.append("🟡 جاهزين (قريبين من نقطة الشراء):")
        for r in readies[:CFG["max_ready"]]:
            dist = (r["pivot"] - r["price"]) / r["price"] * 100
            lines.append(f"• {r['ticker']}: السعر {r['price']:.2f} | نقطة الشراء {r['pivot']:.2f} | باقي {dist:.1f}%")
    else:
        lines.append("🟡 مفيش أسهم جاهزة النهارده.")

    lines.append("")
    lines.append("⚠️ إشارات للمراجعة مش توصية — بص على الشارت قبل ما تدخل.")
    send("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        send("✅ البوت شغال ومتصل بتليجرام.")
    else:
        main()
