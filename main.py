# main.py — Full Production Version with Enhancements

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
from logging.handlers import RotatingFileHandler

# === Logging (to file + console) ===
LOG_FILE = "alerts.log"
handler = RotatingFileHandler(LOG_FILE, maxBytes=1000000, backupCount=3)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s"
)
logger = logging.getLogger(__name__)

# === Flask ===
app = Flask(__name__)
app.last_scan = None
last_alerts = deque(maxlen=20)

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", "0.2"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))

if not all([BOT_TOKEN, CHAT_IDS, FINNHUB_API_KEY]):
    raise RuntimeError("Missing one or more required environment variables.")

# === Globals ===
LIQUID_TICKERS = []
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()
option_cache = {}
option_cache_timestamps = {}
option_cache_lock = threading.Lock()
analyzer = SentimentIntensityAnalyzer()

# === Utilities ===
def get_top_liquid_tickers():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=10)
        symbols = r.json()
        # Filter top by volume using quote endpoint
        liquid = []
        for sym in symbols[:200]:
            q = requests.get(f"https://finnhub.io/api/v1/quote?symbol={sym['symbol']}&token={FINNHUB_API_KEY}").json()
            volume = q.get("v", 0)
            liquid.append((sym['symbol'], volume))
        liquid.sort(key=lambda x: -x[1])
        return [s[0] for s in liquid[:50]]
    except Exception as e:
        logger.warning("Failed to update top tickers: %s", e)
        return LIQUID_TICKERS

def refresh_ticker_list():
    global LIQUID_TICKERS
    LIQUID_TICKERS = get_top_liquid_tickers()
    logger.info("Refreshed top tickers: %s", ', '.join(LIQUID_TICKERS[:5]))

@retry(stop=stop_after_attempt(3), wait=wait_exponential())
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": message[:4096], "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.ok:
            logger.info("Alert sent to %s", chat_id)
        else:
            logger.error("Failed to send to %s: %s", chat_id, resp.text)

        last_alerts.appendleft((datetime.utcnow(), message[:120]))
        time.sleep(1)

def match_ticker(text):
    txt = text.upper()
    return [t for t in LIQUID_TICKERS if re.search(rf"\\b{re.escape(t)}\\b", txt)]

def get_option_data(ticker):
    now = datetime.utcnow()
    with option_cache_lock:
        if ticker in option_cache and now - option_cache_timestamps[ticker] < timedelta(minutes=15):
            return option_cache[ticker]

    try:
        price = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}").json().get("c", 0)
        chain = requests.get(f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}").json()
        best = (None, None, float('inf'))
        for contract in chain.get("data", []):
            for call in contract.get("options", {}).get("CALL", []):
                strike = call.get("strike", 0)
                diff = abs(strike - price)
                if diff < best[2]:
                    last = call.get("lastPrice") or call.get("ask") or 0
                    best = (strike, last, diff)
        option_cache[ticker] = (best[0], best[1])
        option_cache_timestamps[ticker] = now
        return best[0], best[1]
    except Exception as e:
        logger.warning("Option fetch failed: %s", e)
        return None, None

def fetch_and_analyze_news():
    logger.info("Starting scan...")
    now = datetime.utcnow()
    articles = []
    rss = feedparser.parse("https://finance.yahoo.com/news/rssindex")
    for e in rss.entries:
        try:
            dt = datetime.strptime(e.get("published"), "%a, %d %b %Y %H:%M:%S %z")
            if now - dt.replace(tzinfo=None) > timedelta(hours=24):
                continue
        except: continue
        title = e.get("title", "")
        summary = e.get("summary", "")
        articles.append((title, f"{title} {summary}"))

    for title, content in articles:
        h = hashlib.sha256(content.encode()).hexdigest()
        with sent_hashes_lock:
            if h in sent_hashes: continue
            sent_hashes.append(h)
            sent_hashes_timestamps[h] = now

        score = analyzer.polarity_scores(content[:512])["compound"]
        tickers = match_ticker(content)
        if abs(score) < SENTIMENT_THRESHOLD or not tickers:
            continue

        direction = "Bullish" if score > 0 else "Bearish"
        for t in tickers:
            s, p = get_option_data(t)
            if s is None or p is None: continue
            msg = (
                f"🚨 *Market News Alert*\n🕒 {datetime.now():%Y-%m-%d %H:%M}\n📰 {title}\n🔄 {direction}\n\n"
                f"🎯 *Trade Setup*\n• Ticker: {t}\n• Side: {direction}\n• Strike: {s}\n• Exp: 2 weeks\n"
                f"• Est Price: ${p:.2f}\n• Sentiment: {score:.2f}"
            )
            send_telegram_alert(msg)

    app.last_scan = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Scan complete.")

# === Scheduler ===
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MINUTES)
scheduler.add_job(refresh_ticker_list, "interval", hours=6)
scheduler.start()
refresh_ticker_list()

# === Web Dashboard ===
@app.route("/")
def home():
    html = """
    <h1>📈 RealTimeTradeBot Dashboard</h1>
    <p><b>Status:</b> Healthy</p>
    <p><b>Last Scan:</b> {{ last_scan or 'Never' }}</p>
    <p><b>Top Tickers:</b> {{ tickers }}</p>
    <h3>Recent Alerts</h3>
    <ul>
    {% for ts, msg in alerts %}<li><b>{{ ts }}</b>: {{ msg }}</li>{% endfor %}
    </ul>
    <button onclick="fetch('/scan-now').then(r => r.text()).then(alert)">Trigger Scan</button>
    """
    return render_template_string(html, last_scan=app.last_scan, tickers=LIQUID_TICKERS[:5], alerts=list(last_alerts))

@app.route("/health")
def health():
    return jsonify(status="healthy", last_scan=app.last_scan, top_tickers=LIQUID_TICKERS[:5])

@app.route("/scan-now")
def scan_now():
    threading.Thread(target=fetch_and_analyze_news, daemon=True).start()
    return "Scan triggered", 200

@app.route("/test-alert")
def test_alert():
    send_telegram_alert("🚀 Test alert from RealTimeTradeBot")
    return "Alert sent", 200

if __name__ == '__main__':
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
