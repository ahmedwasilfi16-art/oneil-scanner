# الأخبار ومواعيد الأرباح — مشترك بين بوت السوينج وبوت السكالبنج
import datetime as dt

import yfinance as yf

POS = ["beats", "beat estimates", "tops estimates", "raises guidance", "raises outlook", "raises forecast",
       "record revenue", "record quarter", "upgrade", "upgraded", "price target raised", "fda approval",
       "fda approves", "approved by fda", "clearance", "partnership", "strategic agreement", "awarded",
       "wins contract", "contract", "buyback", "repurchase", "to be acquired", "acquisition by",
       "surges", "soars", "jumps", "rallies", "breakthrough"]
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


def news_summary(ticker, hours=48, max_n=2):
    """يرجّع: التصنيف، فيه تخفيف ولا لأ، وأهم العناوين"""
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    titles = []
    for it in items:
        title, when, source = _parse(it)
        if not title or (when is not None and when < cutoff):
            continue
        titles.append((title, when, source))
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
