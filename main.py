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

# === Flask App ===
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
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,NVDA,AMZN').split(',')

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}

# === Sentiment ===
def analyze_sentiment(text: str) -> float:
    positive = {'breakthrough', 'beat', 'soar', 'gain', 'growth', 'strong', 'up'}
    negative = {'loss', 'drop', 'miss', 'fall', 'bearish', 'down'}
    score = sum(1 for w in positive if w in text.lower()) - sum(1 for w in negative if w in text.lower())
    return max(-1, min(1, score / 3))

# === Telegram ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def send_telegram_alert(message: str, chat_ids=CHAT_IDS):
    for cid in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": cid.strip(), "text": message, "parse_mode": "Markdown"}
            resp = requests.post(url, data=payload)
            resp.raise_for_status()
            logger.info(f"Alert sent to {cid}: {message}")
        except Exception as e:
            logger.error(f"Telegram error for {cid}: {e}")

# === Option Data (Polygon) ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        quote = requests.get(f"https://api.polygon.io/v2/last/trade/{ticker}?apiKey={POLYGON_API_KEY}").json()
        last_price = quote.get('results', {}).get('p')
        if not last_price:
            raise ValueError("No quote data")

        chains = requests.get(f"https://api.polygon.io/v3/snapshot/options/{ticker}?apiKey={POLYGON_API_KEY}").json()
        if 'results' not in chains:
            raise ValueError("No options data")

        closest = None
        min_diff = float('inf')
        for opt in chains['results']:
            strike = opt.get('details', {}).get('strike_price')
            price = opt.get('last_quote', {}).get('last_price')
            if strike and price and abs(strike - last_price) < min_diff:
                closest = (strike, price)
                min_diff = abs(strike - last_price)

        return closest if closest else (None, None)
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === News & Sentiment ===
def fetch_marketaux_news() -> List[Dict]:
    try:
        url = f"https://api.marketaux.com/v1/news/all?api_token={MARKETAUX_API_KEY}&limit=20"
        resp = requests.get(url)
        return resp.json().get('data', [])
    except Exception as e:
        logger.warning(f"Marketaux fetch failed: {e}")
        return []

def fetch_yahoo_news() -> List[Dict]:
    feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")
    return [{"title": e.title, "summary": e.summary} for e in feed.entries]

def scan_news_and_alert():
    articles = fetch_marketaux_news() or fetch_yahoo_news()
    for item in articles:
        headline = item.get('title', '')
        content = item.get('summary', headline)
        if not content: continue
        h = hashlib.sha256(content.encode()).hexdigest()
        if h in sent_hashes:
            continue
        sent_hashes.append(h)
        sentiment = analyze_sentiment(content)
        if abs(sentiment) < SENTIMENT_THRESHOLD:
            continue
        for ticker in ticker_list:
            if ticker in content.upper():
                strike, price = get_option_data(ticker)
                if not strike or not price:
                    continue
                msg = f"""
🚨 *Market News Alert*
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📰 {headline}
🔄 {'Bullish' if sentiment > 0 else 'Bearish'} on *{ticker}*
🎯 *Option Strategy*
• Strike: {strike}
• Price: ${price:.2f}
• Sentiment: {sentiment:.2f}
"""
                send_telegram_alert(msg)
                daily_sentiment_scores[ticker].append(sentiment)

# === Mock Alert ===
@app.route('/test/mock_alert')
def trigger_mock():
    send_telegram_alert("🧪 *Mock Alert*: Apple announces breakthrough in AI technology")
    return {"status": "Mock alert sent", "ticker": "AAPL", "headline": "Apple announces breakthrough in AI technology"}

# === Scheduler ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_news_and_alert, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("📡 RealTimeTradeBot started and scheduler running")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
