# === FINAL UPDATED main.py WITH FALLBACK LOGIC ===

import os
import time
import requests
import threading
import hashlib
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

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

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")

SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 10))
TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMZN,NVDA,GOOG').split(',')

# === Globals ===
sent_hashes = deque(maxlen=1000)
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
sentiment_analyzer = SentimentIntensityAnalyzer()

def analyze_sentiment(text: str) -> float:
    return sentiment_analyzer.polarity_scores(text)['compound']

def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            r = requests.post(url, data=data)
            r.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id.strip()}")
        except Exception as e:
            logger.error(f"❌ Failed to send alert to {chat_id.strip()}: {e}")

def get_yf_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        current_price = hist['Close'].iloc[-1]
        date = stock.options[0]
        calls = stock.option_chain(date).calls
        calls_sorted = calls.iloc[(calls['strike'] - current_price).abs().argsort()]
        best_call = calls_sorted.iloc[0]
        return best_call.strike, best_call.lastPrice
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

def get_polygon_quote(ticker: str) -> Optional[float]:
    try:
        url = f"https://api.polygon.io/v2/last/nbbo/{ticker}?apiKey={POLYGON_API_KEY}"
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        return data.get('results', {}).get('askPrice') or data.get('results', {}).get('bidPrice')
    except Exception as e:
        logger.warning(f"Fallback Polygon quote failed for {ticker}: {e}")
        return None

def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    strike, price = get_yf_option_data(ticker)
    if strike is None or price is None:
        logger.warning(f"⚠️ Missing option data for {ticker}. Skipping alert.")
        return
    direction = "Bullish" if sentiment > 0 else "Bearish"
    message = f"""
🚨 *Market News Alert*
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')} (UTC)
📰 {headline}
🔄 {direction}
📡 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {strike}
• Price: ${price:.2f}
• Sentiment: {sentiment:.2f}
• Exit: 50% profit or before expiry
"""
    send_telegram_alert(message)

@app.route('/test/mock_alert')
def mock_alert():
    ticker = request.args.get("ticker", "AAPL")
    headline = f"Test alert for {ticker} on breakthrough news"
    sentiment = 0.7
    source = "MockNews"
    send_trade_alert(ticker, headline, sentiment, source)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

# === Scheduler ===
def run():
    logger.info("⏱️ Starting scheduled alert bot...")
    scheduler = BackgroundScheduler()
    scheduler.start()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    run()
