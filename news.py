# الأخبار ومواعيد الأرباح — مشترك بين بوت السوينج وبوت السكالبنج
import datetime as dt

import requests
import yfinance as yf

POS = ["beats", "beat estimates", "tops estimates", "raises guidance", "raises outlook", "raises forecast",
       "record revenue", "record quarter", "upgrade", "upgraded", "price target raised", "fda approval",
       "fda approves", "approved by fda", "clearance", "partnership", "strategic agreement", "awarded",
       "wins contract", "contract", "buyback", "repurchase", "to be acquired", "acquisition by",
       "surges", "soars", "jumps", "rallies", "breakthrough", "secures", "loan commitment", "grant",
       "department of defense", "selected by", "exceeds", "above expectations", "higher than expected",
       "record", "strategic investment"]
NEG = ["misses", "missed estimates", "falls short", "cuts guidance", "lowers guidance", "cuts outlook",
       "lowers outlook", "downgrade", "downgraded", "price target cut", "lawsuit", "investigation", "probe",
       "subpoena", "recall", "bankruptcy", "chapter 11", "delist", "going concern", "plunges", "tumbles",
       "sinks", "slumps", "short seller", "halted", "resigns", "fraud", "layoffs", "rejects", "crl",
       "complete response letter"]
DILUTION = ["offering", "at-the-market", "atm program", "direct offering", "registered direct",
            "private placement", "warrants", "shelf registration", "reverse split", "reverse stock split"]


def _parse(item):
    """بيدعم الشكل القديم والجديد لأخبار ياهو"""
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    title = c.get("title") or ""
    when = None
    if c.get("pubDate"):
        try:
            when = dt.datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00"))
        except Exception:
            when = None
    elif item.get("providerPublishTime"):
        when = dt.datetime.fromtimestamp(item["providerPublishTime"], tz=dt.timezone.utc)
    prov = c.get("provider")
    source = prov.get("displayName") if isinstance(prov, dict) else (item.get("publisher") or "")
    return title, when, source


def tag_titles(titles):
    text = " | ".join(titles).lower()
    pos = sum(k in text for k in POS)
    neg = sum(k in text for k in NEG)
    dil = any(k in text for k in DILUTION)
    if dil:
        mood = "⛔ طرح أسهم/تخفيف"
    elif pos > neg:
        mood = "🟢 أخبار إيجابية"
    elif neg > pos:
        mood = "🔴 أخبار سلبية"
    elif titles:
        mood = "⚪ أخبار محايدة"
    else:
        mood = "⚪ مفيش أخبار"
    return mood, dil


# ===================== أخبار تريدنج فيو =====================
TV_NEWS = "https://news-headlines.tradingview.com/v2/headlines"
TV_SCAN = "https://scanner.tradingview.com/america/scan"
UA = {"User-Agent": "Mozilla/5.0 (scanner)"}
MAIN_EX = ("NASDAQ", "NYSE", "AMEX")
_SYM = {}


def tv_symbol(ticker):
    """تريدنج فيو محتاج البورصة قبل السهم (NASDAQ:PROF)، فبنجيبها مرة وبنحفظها"""
    if ":" in ticker:
        return ticker
    if ticker in _SYM:
        return _SYM[ticker]
    body = {"markets": ["america"], "columns": ["name"], "range": [0, 10],
            "filter": [{"left": "name", "operation": "equal", "right": ticker.replace("-", ".")}]}
    sym = None
    try:
        rows = requests.post(TV_SCAN, json=body, headers=UA, timeout=20).json().get("data") or []
        main = [r["s"] for r in rows if r["s"].split(":")[0] in MAIN_EX]
        sym = (main or [r["s"] for r in rows] or [None])[0]
    except Exception as e:
        print("تعذر تحديد بورصة", ticker, e)
    _SYM[ticker] = sym
    return sym


def tv_titles(sym, cutoff):
    r = requests.get(TV_NEWS, params={"client": "web", "lang": "en", "symbol": sym}, headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for it in r.json().get("items") or []:
        title = it.get("title") or ""
        when = dt.datetime.fromtimestamp(it["published"], tz=dt.timezone.utc) if it.get("published") else None
        if not title or (when is not None and when < cutoff):
            continue
        prov = it.get("provider")
        source = prov.get("name") if isinstance(prov, dict) else (it.get("source") or prov or "")
        out.append((title, when, source))
    return out


def yahoo_titles(ticker, cutoff):
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []
    out = []
    for it in items:
        title, when, source = _parse(it)
        if not title or (when is not None and when < cutoff):
            continue
        out.append((title, when, source))
    return out


def news_summary(ticker, hours=48, max_n=2, sym=None):
    """يرجّع: التصنيف، فيه تخفيف ولا لأ، وأهم العناوين — من تريدنج فيو، ولو فشل من ياهو"""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    titles = None
    try:
        s = sym or tv_symbol(ticker)
        if s:
            titles = tv_titles(s, cutoff)
    except Exception as e:
        print("أخبار تريدنج فيو مش متاحة لـ", ticker, e)
    if titles is None:
        titles = yahoo_titles(ticker, cutoff)
    mood, dil = tag_titles([t[0] for t in titles])
    top = []
    for title, when, source in titles[:max_n]:
        short = title if len(title) <= 90 else title[:87] + "..."
        top.append(f"   📰 {short}" + (f" ({source})" if source else ""))
    return mood, dil, top


def next_earnings(ticker):
    """تاريخ الأرباح الجاية لو معروف"""
    try:
        cal = yf.Ticker(ticker).calendar
    except Exception:
        return None
    dates = None
    if isinstance(cal, dict):
        dates = cal.get("Earnings Date")
    elif cal is not None and hasattr(cal, "loc"):
        try:
            dates = list(cal.loc["Earnings Date"])
        except Exception:
            dates = None
    if not dates:
        return None
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    today = dt.date.today()
    future = []
    for d in dates:
        try:
            d = d.date() if hasattr(d, "date") and not isinstance(d, dt.date) else d
            if isinstance(d, dt.datetime):
                d = d.date()
            if d >= today:
                future.append(d)
        except Exception:
            pass
    return min(future) if future else None


def earnings_note(ticker, warn_days=14):
    d = next_earnings(ticker)
    if d is None:
        return ""
    days = (d - dt.date.today()).days
    if days <= warn_days:
        return f"   ⚠️ أرباح بعد {days} يوم ({d}) — ممكن فجوة كبيرة"
    return f"   📅 الأرباح الجاية {d}"
