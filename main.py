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
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logging.warning("BeautifulSoup (bs4) not available. Article scraping will be disabled.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

if not BOT_TOKEN or not CHAT_IDS or not FINNHUB_API_KEY:
    raise ValueError("Missing one or more required environment variables.")

ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
option_cache_lock = threading.Lock()
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "finnhub", "url": "https://finnhub.io/api/v1/news", "name": "Finnhub"}
]

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
            data = {
                "chat_id": chat_id.strip(),
                "text": message[:4096],
                "parse_mode": "Markdown"
            }
            logger.info(f"📤 Sending to {chat_id}: {message[:80]}...")
            response = requests.post(url, data=data)
            logger.info(f"✅ Telegram API response: {response.status_code} - {response.text}")
            response.raise_for_status()
            time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Failed to send to {chat_id}: {e}")
            raise

def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        return
    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {headline}
🔄 {direction}
📡 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {strike}
• Expiration: 2 weeks
• Est. Contract Price: ${price:.2f}
• Reason: Sentiment score {sentiment:.2f}
• Entry: ASAP
• Exit: 50% profit or 3 days before expiration
"""
    send_telegram_alert(message)

@app.route('/test/mock_alert')
def trigger_mock_alert():
    ticker = "AAPL"
    headline = "Apple announces breakthrough in AI technology"
    sentiment = 0.6
    source = "MockSource"
    send_trade_alert(ticker, headline, sentiment, source)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

# other functions (verify_symbol, get_option_data, fetch_and_analyze_news, etc.) remain unchanged

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.add_job(send_daily_summary, 'cron', hour=9, minute=0)
    scheduler.start()
    fetch_and_analyze_news()
    logger.info("Forced initial scan")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
