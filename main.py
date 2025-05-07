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
from flask import Flask, jsonify, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup
import yfinance as yf

# === Setup Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__, template_folder="templates")

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 10))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", "AAPL,TSLA,MSFT,NVDA,AMZN,GOOG,META,JPM,AMD").split(',')

# === Validate Environment ===
if not BOT_TOKEN or not CHAT_IDS:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or CHAT_IDS")
if not POLYGON_API_KEY or not MARKETAUX_API_KEY:
    raise ValueError("Missing API keys for Polygon or Marketaux")

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
sent_hashes_lock = threading.Lock()
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

# === Dashboard ===
@app.route('/')
def dashboard():
    return render_template("dashboard.html", tickers=ticker_list)

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route('/telegram/list_tickers')
def list_tickers():
    with ticker_list_lock:
        return jsonify({"tracked": ticker_list})

@app.route('/telegram/add_ticker_<symbol>')
def add_ticker(symbol):
    symbol = symbol.upper()
    with ticker_list_lock:
        if symbol in ticker_list:
            return f"{symbol} already tracked"
        ticker_list.append(symbol)
        daily_sentiment_scores[symbol] = []
        return f"{symbol} added"

@app.route('/telegram/remove_ticker_<symbol>')
def remove_ticker(symbol):
    symbol = symbol.upper()
    with ticker_list_lock:
        if symbol not in ticker_list:
            return f"{symbol} not found"
        ticker_list.remove(symbol)
        daily_sentiment_scores.pop(symbol, None)
        return f"{symbol} removed"

# === Telegram ===
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
            logger.error(f"❌ Telegram failed for {chat_id.strip()}: {e}")

# === Sentiment ===
def analyze_sentiment(text: str) -> float:
    pos = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    neg = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    text = text.lower()
    p_score = sum(word in text for word in pos)
    n_score = sum(word in text for word in neg)
    return 0.5 if p_score > n_score else -0.5 if n_score > p_score else 0.0

def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    now = datetime.now()
    if ticker in option_cache and now - option_cache_timestamps[ticker] < timedelta(minutes=15):
        return option_cache[ticker]
    try:
        stock = yf.Ticker(ticker)
        current_price = stock.history(period="1d")["Close"].iloc[-1]
        options = stock.option_chain(stock.options[0]).calls
        options['diff'] = abs(options['strike'] - current_price)
        atm = options.sort_values("diff").iloc[0]
        result = (atm['strike'], atm['lastPrice'])
        option_cache[ticker] = result
        option_cache_timestamps[ticker] = now
        return result
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

def send_trade_alert(ticker, headline, sentiment, source):
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        logger.warning(f"Missing option data for {ticker}. Skipping alert.")
        return
    message = f"""
🚨 *Market News Alert*
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📰 {headline}
📡 {source}
🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: {'Long Call' if sentiment > 0 else 'Long Put'}
• Strike: {strike}
• Est. Price: ${price:.2f}
• Sentiment: {sentiment:.2f}
"""
    send_telegram_alert(message)

def fetch_marketaux_news():
    try:
        url = f"https://api.marketaux.com/v1/news/all?filter_entities=true&limit=10&api_token={MARKETAUX_API_KEY}"
        response = requests.get(url).json()
        return [
            {
                "title": a["title"],
                "summary": a.get("description", ""),
                "content": a.get("description", "") + " " + a.get("title", ""),
                "source": a.get("source", {}).get("name", "Marketaux"),
            }
            for a in response.get("data", [])
        ]
    except Exception as e:
        logger.error(f"Marketaux error: {e}")
        return []

def match_ticker(text: str) -> List[str]:
    with ticker_list_lock:
        return [t for t in ticker_list if re.search(r'\b' + re.escape(t) + r'\b', text.upper())]

def scan_news_and_alert():
    logger.info("🔍 Scanning news sources...")
    articles = fetch_marketaux_news()
    count = 0
    for article in articles:
        h = hashlib.sha256(article["content"].encode()).hexdigest()
        with sent_hashes_lock:
            if h in sent_hashes:
                continue
            sent_hashes.append(h)
        sentiment = analyze_sentiment(article["content"])
        if abs(sentiment) >= SENTIMENT_THRESHOLD:
            matched = match_ticker(article["content"])
            for t in matched:
                send_trade_alert(t, article["title"], sentiment, article["source"])
                count += 1
                if count >= 2:
                    return

# === Scheduler and Routes ===
@app.route('/test/mock_alert')
def mock_alert():
    send_trade_alert("AAPL", "Apple AI breakthrough", 0.6, "MockSource")
    return {"status": "sent", "ticker": "AAPL"}

def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_news_and_alert, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    scan_news_and_alert()
    logger.info("✅ RealTimeTradeBot initialized")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
