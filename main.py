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

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Optional: BeautifulSoup for full article scraping
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.warning("BeautifulSoup (bs4) not available. Article scraping will be disabled.")

# === Flask Setup ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

if not BOT_TOKEN or not CHAT_IDS or not FINNHUB_API_KEY:
    raise ValueError("Missing one or more required environment variables.")

# === Globals ===
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

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message, chat_ids=CHAT_IDS):
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Alert sent to chat ID {chat_id.strip()}: {message}")
        except Exception as e:
            logger.error(f"Failed to send alert to {chat_id.strip()}: {e}")

# === Option Data Fetch ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]
    try:
        quote_response = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}")
        quote_response.raise_for_status()
        current_price = quote_response.json().get('c', 0)
        if not current_price:
            return None, None
        option_response = requests.get(f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}")
        option_response.raise_for_status()
        option_data = option_response.json()
        atm_strike, option_price, min_diff = None, None, float('inf')
        for contract in option_data.get('data', []):
            for option in contract.get('options', {}).get('CALL', []):
                strike = option['strike']
                diff = abs(strike - current_price)
                if diff < min_diff:
                    atm_strike = strike
                    option_price = option.get('lastPrice', 0) or option.get('ask', 0)
                    min_diff = diff
        with option_cache_lock:
            option_cache[ticker] = (atm_strike, option_price)
            option_cache_timestamps[ticker] = datetime.now()
        return atm_strike, option_price
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === Alert ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        logger.warning(f"Missing option data for {ticker}. Skipping alert.")
        return
    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {headline}
🔄 {direction}
📱 {source}

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

# === Mock Alert ===
@app.route('/test/mock_alert')
def trigger_mock_alert():
    ticker = "AAPL"
    headline = "Apple announces breakthrough in AI technology"
    sentiment = 0.6
    source = "MockSource"
    send_trade_alert(ticker, headline, sentiment, source)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

# === Main Runner ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.start()
    logger.info("Starting scheduler")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
