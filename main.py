import os
import time
import logging
import threading
import requests
import feedparser
import hashlib
import re

from collections import deque
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# === Logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)
app.last_scan = None
app.alert_log = deque(maxlen=50)

# === Env Variables ===
BOT_TOKEN             = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS_RAW          = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
FINNHUB_API_KEY       = os.getenv("FINNHUB_API_KEY", "").strip()
SENTIMENT_THRESHOLD   = float(os.getenv("SENTIMENT_THRESHOLD", "0.2"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
LIQUID_TICKERS        = []

# === Ticker Aliases ===
TICKER_ALIASES = {
    "APPLE": "AAPL", "TESLA": "TSLA", "MICROSOFT": "MSFT", "GOOGLE": "GOOG", "ALPHABET": "GOOG",
    "META": "META", "AMAZON": "AMZN", "NVIDIA": "NVDA", "AMD": "AMD", "INTEL": "INTC",
    "EXXON": "XOM", "CHEVRON": "CVX", "BERKSHIRE": "BRK.B"
}

# === Validate ===
missing = []
if not BOT_TOKEN:       missing.append("TELEGRAM_BOT_TOKEN")
if not CHAT_IDS_RAW:    missing.append("TELEGRAM_CHAT_IDS")
if not FINNHUB_API_KEY: missing.append("FINNHUB_API_KEY")
if missing:
    raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

CHAT_IDS = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]

# === State ===
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()

option_cache = {}
option_cache_timestamps = {}
option_cache_lock = threading.Lock()

# === Sentiment ===
analyzer = SentimentIntensityAnalyzer()

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": message[:4096], "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.ok:
            logger.info("Alert sent to %s", chat_id)
        else:
            logger.error("Telegram error for chat %s: %s", chat_id, resp.text)

# === Ticker Utilities ===
def get_top_liquid_tickers():
    try:
        res = requests.get(f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}")
        res.raise_for_status()
        symbols = res.json()
        top = [s["symbol"] for s in symbols if s["symbol"].isupper()][:50]
        return top
    except Exception as e:
        logger.error("Error getting top tickers: %s", e)
        return ["AAPL", "TSLA", "MSFT", "NVDA", "GOOG", "AMZN"]

def match_ticker(text: str):
    txt = text.upper()
    found = set()
    for t in LIQUID_TICKERS:
        if re.search(rf"\b{re.escape(t)}\b", txt):
            found.add(t)
    for name, t in TICKER_ALIASES.items():
        if re.search(rf"\b{name}\b", txt):
            found.add(t)
    return list(found)

def get_option_data(ticker: str):
    now = datetime.utcnow()
    with option_cache_lock:
        if ticker in option_cache and now - option_cache_timestamps[ticker] < timedelta(minutes=15):
            return option_cache[ticker]
    try:
        q = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}").json()
        price = q.get("c") or 0
        if not price:
            return None, None
        oc = requests.get(f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}").json()
        best = (None, None, float("inf"))
        for contract in oc.get("data", []):
            for call in contract.get("options", {}).get("CALL", []):
                strike = call.get("strike", 0)
                diff = abs(strike - price)
                if diff < best[2]:
                    last = call.get("lastPrice") or call.get("ask") or 0
                    best = (strike, last, diff)
        result = (best[0], best[1]) if best[0] else (None, None)
        with option_cache_lock:
            option_cache[ticker] = result
            option_cache_timestamps[ticker] = now
        return result
    except Exception as e:
        logger.warning("get_option_data(%s) failed: %s", ticker, e)
        return None, None

# === News Scan ===
def fetch_and_analyze_news():
    logger.info("Starting scan...")
    now = datetime.utcnow()
    sources = [("rss", "https://finance.yahoo.com/news/rssindex")]
    articles = []

    for kind, url in sources:
        if kind == "rss":
            feed = feedparser.parse(url)
            for e in feed.entries:
                try:
                    dt = datetime.strptime(e.published, "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
                    if now - dt > timedelta(hours=24): continue
                except:
                    continue
                title = e.get("title", "")
                summary = e.get("summary", "")
                articles.append((title, f"{title} {summary}"))

    for title, content in articles:
        h = hashlib.sha256(content.encode()).hexdigest()
        with sent_hashes_lock:
            cutoff = now - timedelta(hours=24)
            for k in list(sent_hashes_timestamps):
                if sent_hashes_timestamps[k] < cutoff:
                    sent_hashes_timestamps.pop(k, None)
                    try: sent_hashes.remove(k)
                    except: pass
            if h in sent_hashes: continue
            sent_hashes.append(h)
            sent_hashes_timestamps[h] = now

        score = analyzer.polarity_scores(content[:512])["compound"]
        tickers = match_ticker(content)
        if abs(score) < SENTIMENT_THRESHOLD or not tickers: continue
        direction = "Bullish" if score > 0 else "Bearish"

        for t in tickers:
            s, p = get_option_data(t)
            if s is None or p is None:
                logger.info("Skipping %s: no option data", t)
                continue
            msg = (
                f"🚨 *Market News Alert*\n"
                f"🕒 {datetime.now():%Y-%m-%d %H:%M}\n"
                f"📰 {title}\n"
                f"🔄 {direction}\n\n"
                f"🎯 *Trade Setup*\n"
                f"• Ticker: {t}\n"
                f"• Side: {direction}\n"
                f"• Strike: {s}\n"
                f"• Expiration: 2 weeks out\n"
                f"• Est Price: ${p:.2f}\n"
                f"• Sentiment score: {score:.2f}\n"
            )
            send_telegram_alert(msg)
            app.alert_log.appendleft({"time": datetime.now().isoformat(), "ticker": t, "score": score, "title": title})
    app.last_scan = now.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Scan complete.")

# === Scheduler ===
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MINUTES)
scheduler.add_job(lambda: LIQUID_TICKERS.clear() or LIQUID_TICKERS.extend(get_top_liquid_tickers()), "interval", hours=12)
scheduler.start()

# === Routes ===
@app.route("/")
def home():
    alerts_html = "".join(f"<li>{a['time']} - {a['ticker']} ({a['score']:.2f}): {a['title']}</li>" for a in list(app.alert_log)[:5])
    html = f"""
    <h1>✅ RealTimeTradeBot</h1>
    <p>Status: <strong>Healthy</strong></p>
    <p>Last Scan: <strong>{app.last_scan or 'Never'}</strong></p>
    <p><button onclick="fetch('/scan-now').then(r => r.text()).then(alert)">Run Scan Now</button></p>
    <h3>📢 Recent Alerts</h3>
    <ul>{alerts_html or "<li>No alerts yet.</li>"}</ul>
    """
    return render_template_string(html)

@app.route("/health")
def health():
    return jsonify(status="healthy", last_scan=app.last_scan)

@app.route("/scan-now")
def scan_now():
    threading.Thread(target=fetch_and_analyze_news, daemon=True).start()
    return "On-demand scan started.", 200

@app.route("/test-alert")
def test_alert():
    send_telegram_alert("🚀 Test alert from RealTimeTradeBot!")
    return "Test alert sent.", 200

# === Launch ===
if __name__ == "__main__":
    LIQUID_TICKERS.extend(get_top_liquid_tickers())
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
