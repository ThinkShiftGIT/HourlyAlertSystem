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
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN').split(',')

if not BOT_TOKEN or not CHAT_IDS or not POLYGON_API_KEY or not MARKETAUX_API_KEY:
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

# === Sentiment Analysis ===
def analyze_sentiment(text: str) -> float:
    positive = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    negative = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    text = text.lower()
    pos_count = sum(text.count(word) for word in positive)
    neg_count = sum(text.count(word) for word in negative)
    if pos_count > neg_count:
        return 0.5
    elif neg_count > pos_count:
        return -0.5
    return 0.0

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=3, max=10))
def send_telegram_alert(message, chat_ids=CHAT_IDS):
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id.strip()}")
        except Exception as e:
            logger.error(f"❌ Failed to send alert to {chat_id.strip()}: {e}")

# === Option Data ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]
    try:
        quote = requests.get(f"https://api.polygon.io/v2/last/nbbo/{ticker}?apiKey={POLYGON_API_KEY}").json()
        option_chain = requests.get(f"https://api.polygon.io/v3/snapshot/options/{ticker}?apiKey={POLYGON_API_KEY}").json()
        last = quote.get('results', {}).get('ask', 0)
        contracts = option_chain.get('results', {}).get('breakdown', [])
        strike, price = None, None
        for contract in contracts:
            if contract['option_type'] == 'call':
                strike = contract['strike_price']
                price = contract['last_quote'].get('ask', 0)
                break
        with option_cache_lock:
            option_cache[ticker] = (strike, price)
            option_cache_timestamps[ticker] = datetime.now()
        return strike, price
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === Alerts ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        logger.warning(f"⚠️ Missing option data for {ticker}. Skipping alert.")
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
def mock_alert():
    send_trade_alert("AAPL", "Apple launches AI breakthrough", 0.6, "MockSource")
    return {"status": "Mock alert sent", "ticker": "AAPL"}

# === News Fetch ===
def fetch_and_analyze_news():
    try:
        response = requests.get(
            f"https://api.marketaux.com/v1/news/all?filter_entities=true&language=en&api_token={MARKETAUX_API_KEY}"
        )
        articles = response.json().get("data", [])
        logger.info(f"🔍 Scanned {len(articles)} articles")

        alerts_sent = 0
        for article in articles:
            content = f"{article.get('title', '')} {article.get('description', '')}"
            h = hashlib.sha256(content.encode()).hexdigest()

            with sent_hashes_lock:
                if h in sent_hashes:
                    continue
                sent_hashes.append(h)
                sent_hashes_timestamps[h] = datetime.now()

            sentiment = analyze_sentiment(content)
            if abs(sentiment) >= SENTIMENT_THRESHOLD:
                for ticker in ticker_list:
                    if re.search(rf"\b{ticker}\b", content.upper()) and alerts_sent < 2:
                        send_trade_alert(ticker, article.get('title', ''), sentiment, article.get('source', 'Marketaux'))
                        with sentiment_scores_lock:
                            daily_sentiment_scores[ticker].append(sentiment)
                        alerts_sent += 1
    except Exception as e:
        logger.error(f"News fetch failed: {e}")

# === Scheduler Setup ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    fetch_and_analyze_news()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
