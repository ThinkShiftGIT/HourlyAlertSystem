import os
import time
import requests
import threading
import feedparser
import hashlib
import logging
import re
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return jsonify(status="healthy", last_scan=getattr(app, 'last_scan_time', 'never'))

@app.route('/test-alert')
def test_alert():
    try:
        send_telegram_alert("🚀 Test alert: bot is online.")
        return "Test alert sent."
    except Exception as e:
        logger.error("Test-alert failed", exc_info=e)
        return f"Test-alert failed: {e}", 500

@app.route('/scan-now')
def scan_now():
    threading.Thread(target=fetch_and_analyze_news, daemon=True).start()
    return "Scan triggered."

# === Environment Variables ===
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN") or ""
CHAT_IDS          = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY") or ""
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", "0.5"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
LIQUID_TICKERS    = [t.strip() for t in os.getenv("LIQUID_TICKERS", "AAPL,TSLA,SPY").split(",")]

if not BOT_TOKEN or not CHAT_IDS or not FINNHUB_API_KEY:
    logger.error("Please set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, and FINNHUB_API_KEY")
    exit(1)

# === Thread-Safe State ===
sent_hashes            = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock       = threading.Lock()

option_cache            : Dict[str, Tuple[Optional[float],Optional[float]]] = {}
option_cache_timestamps : Dict[str, datetime] = {}
option_cache_lock       = threading.Lock()

# === News Sources ===
news_sources = [
    {"type": "rss",    "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "finnhub","url": "https://finnhub.io/api/v1/news",        "name": "Finnhub"}
]

# === Sentiment Analyzer (VADER) ===
sentiment_analyzer = SentimentIntensityAnalyzer()

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message[:4096],
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, data=data)
        try:
            resp.raise_for_status()
            logger.info(f"Alert sent to {chat_id}")
        except Exception as e:
            # log the API error body if available
            err = resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else resp.text
            logger.error(f"Telegram API HTTPError for chat {chat_id}: {err}")
            raise

# === Ticker Matching ===
def match_ticker(text: str):
    text = text.upper()
    return [t for t in LIQUID_TICKERS if re.search(rf'\b{re.escape(t)}\b', text)]

# === Fetch Full Article ===
def get_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        paras = soup.find_all('p')
        return " ".join(p.get_text(strip=True) for p in paras)
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return ""

# === Option Data via Finnhub ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        ts = option_cache_timestamps.get(ticker)
        if ts and ts > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]

    try:
        quote = requests.get(
            f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
        ).json()
        current = quote.get("c", 0)
        if not current:
            raise ValueError("No current price")

        chain = requests.get(
            f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}"
        ).json().get("data", [])

        atm, price = None, None
        best = float("inf")
        for lot in chain:
            for opt in lot.get("options", {}).get("CALL", []):
                diff = abs(opt["strike"] - current)
                if diff < best:
                    best, atm, price = diff, opt["strike"], opt.get("lastPrice") or opt.get("ask") or 0

        result = (atm, price) if atm is not None else (None, None)
        with option_cache_lock:
            option_cache[ticker] = result
            option_cache_timestamps[ticker] = datetime.now()
        return result

    except Exception as e:
        logger.error(f"Error fetching options for {ticker}: {e}")
        return None, None

# === Send Trade Alert ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    atm, opt_price = get_option_data(ticker)
    if atm is None or opt_price is None:
        logger.warning(f"Skip {ticker}: no option data")
        return

    msg = f"""
🚨 *Market News Alert*
🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC
📰 {headline}
🔄 {direction}
📡 Source: {source}

🎯 Trade Setup
• Ticker: *{ticker}*
• Strategy: Long {'Call' if direction=='Bullish' else 'Put'}
• Strike: {atm}
• Expiry: 2 weeks
• Est. Price: ${opt_price:.2f}
• Sentiment: {sentiment:.2f}
"""
    send_telegram_alert(msg)

# === Main Scanner ===
def fetch_and_analyze_news():
    try:
        for src in news_sources:
            logger.info(f"Scanning {src['name']}…")
            items = []

            if src["type"] == "rss":
                feed = feedparser.parse(src["url"])
                for e in feed.entries:
                    pub = e.get("published") or e.get("updated","")
                    if pub:
                        try:
                            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
                            if datetime.now(dt.tzinfo) - dt > timedelta(hours=24):
                                continue
                        except:
                            pass
                    content = f"{e.title} {e.get('summary','')} {get_full_article(e.link)}"
                    items.append((e.title, content, src["name"]))

            else:  # finnhub
                for tk in LIQUID_TICKERS:
                    r = requests.get(f"{src['url']}?symbol={tk}&token={FINNHUB_API_KEY}")
                    r.raise_for_status()
                    for it in r.json():
                        ts = it.get("datetime",0)
                        if datetime.now() - datetime.fromtimestamp(ts) > timedelta(hours=24):
                            continue
                        txt = f"{it.get('headline','')} {it.get('summary','')}"
                        items.append((it.get("headline",""), txt, src["name"]))

            for title, txt, name in items:
                hsh = hashlib.sha256(txt.encode()).hexdigest()
                with sent_hashes_lock:
                    # clean up old
                    cutoff = datetime.now() - timedelta(hours=24)
                    for k,v in list(sent_hashes_timestamps.items()):
                        if v < cutoff:
                            sent_hashes.remove(k)
                            del sent_hashes_timestamps[k]
                    if hsh in sent_hashes:
                        continue
                    sent_hashes.append(hsh)
                    sent_hashes_timestamps[hsh] = datetime.now()

                score = sentiment_analyzer.polarity_scores(txt[:512])["compound"]
                if abs(score) >= SENTIMENT_THRESHOLD:
                    for tk in match_ticker(txt):
                        send_trade_alert(tk, title, score, name)

        app.last_scan_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error("Error in fetch_and_analyze_news", exc_info=e)

# === Run as Service ===
def main():
    # 1) start background scheduler
    sched = BackgroundScheduler()
    sched.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MINUTES)
    sched.start()
    logger.info("Scheduler started; first scan in %d minutes", SCAN_INTERVAL_MINUTES)

    # 2) start web server via Waitress (blocks)
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

if __name__ == "__main__":
    main()
