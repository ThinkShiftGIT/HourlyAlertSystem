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
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from dotenv import load_dotenv
import yfinance as yf

# Load environment variables
load_dotenv()

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running! Navigate to /dashboard for UI."

@app.route('/dashboard')
def dashboard():
    return render_template("dashboard.html")

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 10))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,NVDA,META,AMZN').split(',')

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

analyzer = SentimentIntensityAnalyzer()

news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "marketaux", "url": "https://api.marketaux.com/v1/news/all", "name": "Marketaux"}
]

# === Utilities ===

def analyze_sentiment(text: str) -> float:
    return analyzer.polarity_scores(text)['compound']

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=10))
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


def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        stock = yf.Ticker(ticker)
        price = stock.history(period="1d").iloc[-1].Close
        options = stock.option_chain().calls
        options['diff'] = abs(options['strike'] - price)
        row = options.sort_values('diff').iloc[0]
        return row['strike'], row['lastPrice']
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None


def match_ticker(text: str) -> List[str]:
    with ticker_list_lock:
        return [t for t in ticker_list if re.search(r'\\b' + re.escape(t) + r'\\b', text.upper())]


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


def fetch_and_analyze_news():
    try:
        for source in news_sources:
            logger.info(f"Scanning {source['name']}...")
            articles = []
            if source['type'] == 'rss':
                for entry in feedparser.parse(source['url']).entries:
                    articles.append({"title": entry.title, "content": entry.summary, "source": source['name']})
            elif source['type'] == 'marketaux':
                params = {"api_token": MARKETAUX_API_KEY, "language": "en", "limit": 50}
                response = requests.get(source['url'], params=params)
                data = response.json().get("data", [])
                for item in data:
                    articles.append({"title": item.get("title"), "content": item.get("description", ""), "source": source['name']})

            for article in articles:
                content = article['content'] or ''
                h = hashlib.sha256((article['title'] + content).encode()).hexdigest()
                with sent_hashes_lock:
                    if h in sent_hashes:
                        continue
                    sent_hashes.append(h)
                    sent_hashes_timestamps[h] = datetime.now()

                sentiment = analyze_sentiment(article['title'] + " " + content)
                if abs(sentiment) >= SENTIMENT_THRESHOLD:
                    tickers = match_ticker(article['title'] + " " + content)
                    if tickers:
                        send_trade_alert(tickers[0], article['title'], sentiment, article['source'])
                        with sentiment_scores_lock:
                            daily_sentiment_scores[tickers[0]].append(sentiment)

    except Exception as e:
        logger.error(f"Error during news scan: {e}")


@app.route('/test/mock_alert')
def trigger_mock_alert():
    send_trade_alert("AAPL", "Apple announces breakthrough in AI technology", 0.65, "Mock")
    return {"status": "Mock alert sent", "ticker": "AAPL"}


def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("Scheduler started. Initial news scan running...")
    fetch_and_analyze_news()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
