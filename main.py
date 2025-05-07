import os
import time
import requests
import threading
import hashlib
import logging
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, Tuple, Optional, List
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__)

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN').split(',')

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
sent_hashes_lock = threading.Lock()
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
option_cache_lock = threading.Lock()

latest_alerts = []

# === Flask Routes ===
@app.route("/")
def dashboard():
    return render_template("dashboard.html", alerts=latest_alerts)

@app.route("/health")
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route("/test/mock_alert")
def trigger_mock_alert():
    ticker = "AAPL"
    headline = "Apple announces breakthrough in AI technology"
    sentiment = 0.7
    source = "MockSource"
    send_trade_alert(ticker, headline, sentiment, source)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

# === Telegram ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id.strip()}")
        except Exception as e:
            logger.error(f"Failed to send alert to {chat_id.strip()}: {e}")

# === Sentiment ===
def analyze_sentiment(text: str) -> float:
    pos = ['growth', 'profit', 'up', 'gain', 'beat', 'bullish']
    neg = ['loss', 'drop', 'fall', 'miss', 'bearish']
    score = sum(word in text.lower() for word in pos) - sum(word in text.lower() for word in neg)
    return max(-1.0, min(1.0, score / 3.0))

# === Polygon + Marketaux Integration ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=10):
            return option_cache[ticker]
    try:
        quote = requests.get(f"https://api.polygon.io/v2/last/nbbo/{ticker}?apiKey={POLYGON_API_KEY}").json()
        current_price = quote.get('results', {}).get('ask', {}).get('price') or 0
        strike = round(current_price)
        option_price = round(current_price * 0.07, 2)
        with option_cache_lock:
            option_cache[ticker] = (strike, option_price)
            option_cache_timestamps[ticker] = datetime.now()
        return strike, option_price
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === Alerts ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if not strike or not price:
        logger.warning(f"Missing option data for {ticker}. Skipping alert.")
        return
    message = f"""
🚨 *Market Alert*
📰 {headline}
🔄 {direction}
📊 Sentiment: {sentiment:.2f}
🎯 Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {strike}
• Est. Price: ${price:.2f}
• Entry: ASAP | Exit: +50% or 3 days pre-expiry
"""
    send_telegram_alert(message)
    with sentiment_scores_lock:
        daily_sentiment_scores[ticker].append(sentiment)
    latest_alerts.append({"ticker": ticker, "headline": headline, "time": datetime.now().strftime('%H:%M')})
    if len(latest_alerts) > 10:
        latest_alerts.pop(0)

# === News Scanner ===
def scan_marketaux_news():
    try:
        url = f"https://api.marketaux.com/v1/news/all?filter_entities=true&language=en&api_token={MARKETAUX_API_KEY}"
        data = requests.get(url).json().get("data", [])
        for item in data:
            h = hashlib.sha256(item["title"].encode()).hexdigest()
            with sent_hashes_lock:
                if h in sent_hashes:
                    continue
                sent_hashes.append(h)
            sentiment = analyze_sentiment(item["title"])
            if abs(sentiment) >= SENTIMENT_THRESHOLD:
                for t in ticker_list:
                    if t in item["title"]:
                        send_trade_alert(t, item["title"], sentiment, "Marketaux")
                        break
    except Exception as e:
        logger.error(f"Error in Marketaux scan: {e}")

# === Scheduler ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_marketaux_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("Scheduler started")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
