#!/usr/bin/env python3
import os
import sys
import time
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
import feedparser
import requests
import hashlib
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from prometheus_client import Counter, Gauge, start_http_server
import sentry_sdk
from waitress import serve

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# === Sentry (optional) ===
if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        traces_sample_rate=1.0
    )
    logger.info("Sentry initialized")

# === Flask App & Metrics ===
app = Flask(__name__)
alerts_sent = Counter('tradebot_alerts_sent', 'Number of Telegram alerts sent')
articles_processed = Counter('tradebot_articles_processed', 'Number of news articles processed')
errors = Counter('tradebot_errors', 'Number of errors encountered')
jobs_running = Gauge('tradebot_jobs_running', 'Number of fetch jobs running')

# === Environment Variables ===
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
raw_ids = os.getenv('TELEGRAM_CHAT_IDS', '')
CHAT_IDS = [cid.strip() for cid in raw_ids.split(',') if cid.strip()]
if not CHAT_IDS:
    logger.error("No valid TELEGRAM_CHAT_IDS set. Exiting.")
    sys.exit(1)
for cid in CHAT_IDS:
    if not cid.isdigit():
        logger.error(f"Invalid TELEGRAM_CHAT_ID: {cid}")
        sys.exit(1)
logger.info(f"Telegram chat IDs: {CHAT_IDS}")

FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
THRESHOLD = float(os.getenv('SENTIMENT_THRESHOLD', '0.1'))
INTERVAL = int(os.getenv('SCAN_INTERVAL_MINUTES', '5'))
LIQUID_TICKERS = [t.strip().upper() for t in os.getenv(
    'LIQUID_TICKERS',
    'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN'
).split(',')]

# Validate critical vars
if not BOT_TOKEN or not FINNHUB_API_KEY:
    logger.error("Missing TELEGRAM_BOT_TOKEN or FINNHUB_API_KEY. Exiting.")
    sys.exit(1)

# === Deduplication ===
sent_hashes = set()
hash_lock = threading.Lock()

def is_duplicate(h):
    with hash_lock:
        if h in sent_hashes:
            return True
        sent_hashes.add(h)
        return False

# === Sentiment Analyzer ===
sentiment_analyzer = SentimentIntensityAnalyzer()

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def send_telegram_alert(msg: str):
    from requests.exceptions import HTTPError as ReqHTTPError
    for cid in CHAT_IDS:
        resp = None
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={'chat_id': cid, 'text': msg[:4096]}
            )
            resp.raise_for_status()
            alerts_sent.inc()
            time.sleep(1)
        except ReqHTTPError:
            try:
                error_info = resp.json()
            except Exception:
                error_info = resp.text if resp is not None else "<no response>"
            logger.error(f"Telegram API HTTPError for chat {cid}: {error_info}")
            errors.inc()
            raise
        except Exception as e:
            logger.error(f"Unexpected error sending to chat {cid}: {e}")
            errors.inc()
            raise

# === Fetch & Analyze ===
news_sources = [
    {'type': 'rss', 'url': 'https://finance.yahoo.com/news/rssindex'},
    {'type': 'rss', 'url': 'https://feeds.reuters.com/reuters/businessNews'}
]

@app.route('/')
def home():
    return "✅ Bot running"

@app.route('/health')
def health():
    return jsonify(status='healthy', last_scan=getattr(app, 'last_scan_time', 'never'))

@app.route('/test-alert')
def test_alert():
    try:
        send_telegram_alert("🚀 Test alert: bot is online.")
        return "OK"
    except Exception as e:
        logger.exception("Test-alert failed")
        return f"ERROR: {e}", 500

def fetch_and_alert():
    jobs_running.inc()
    try:
        for src in news_sources:
            feed = feedparser.parse(src['url'])
            for e in feed.entries[:10]:
                content = f"{e.title} {e.get('summary','')}"
                h = hashlib.sha256(content.encode()).hexdigest()
                if is_duplicate(h):
                    continue
                score = sentiment_analyzer.polarity_scores(content)['compound']
                if abs(score) < THRESHOLD:
                    continue
                tickers = [t for t in LIQUID_TICKERS if t in content.upper()]
                if tickers:
                    msg = f"{e.title}\nSentiment: {score:.2f}\nTickers: {', '.join(tickers)}"
                    send_telegram_alert(msg)
                articles_processed.inc()
        app.last_scan_time = datetime.utcnow().isoformat()
    except Exception as e:
        logger.error(f"Error in fetch_and_alert: {e}")
        errors.inc()
    finally:
        jobs_running.dec()

def main():
    start_http_server(8000)
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_alert, 'interval', minutes=INTERVAL)
    scheduler.start()
    serve(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')))

if __name__ == '__main__':
    main()
