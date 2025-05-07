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
from typing import Dict, Tuple, Optional, List
from flask import Flask, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route('/dashboard')
def dashboard():
    return render_template("dashboard.html")

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 10))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

if not BOT_TOKEN or not CHAT_IDS or not MARKETAUX_API_KEY or not POLYGON_API_KEY:
    raise ValueError("Missing one or more required environment variables.")

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

# === Sentiment Analysis ===
def analyze_sentiment(text: str) -> float:
    positive_words = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    negative_words = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    text = text.lower()
    pos_count = sum(text.count(word) for word in positive_words)
    neg_count = sum(text.count(word) for word in negative_words)
    if pos_count > neg_count:
        return 0.5
    elif neg_count > pos_count:
        return -0.5
    return 0.0

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message, chat_ids=CHAT_IDS):
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id.strip()}")
        except Exception as e:
            logger.error(f"Failed to send alert to {chat_id.strip()}: {e}")

# === Ticker Management ===
def list_tickers() -> str:
    with ticker_list_lock:
        return "Tracked tickers:\n" + "\n".join(ticker_list)

# === Mock Alert ===
@app.route('/test/mock_alert')
def trigger_mock_alert():
    ticker = "AAPL"
    headline = "Apple announces breakthrough in AI technology"
    sentiment = 0.6
    source = "MockSource"
    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {headline}
🔄 Bullish
📡 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long Call
• Strike: 195
• Expiration: 2 weeks
• Est. Contract Price: $3.45
• Reason: Sentiment score {sentiment:.2f}
• Entry: ASAP
• Exit: 50% profit or 3 days before expiration
"""
    send_telegram_alert(message)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

# === Background Task Placeholder ===
def dummy_scan():
    logger.info("Dummy scan executed (replace with actual fetch logic).")

# === Main Runner ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(dummy_scan, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("📈 Scheduler started")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
