import os
import time
import json
import logging
import requests
import feedparser
import hashlib
import threading
from datetime import datetime, timedelta
from collections import deque
from typing import List, Tuple, Dict, Optional
from flask import Flask, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
import yfinance as yf

# === Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
alerts_file = "alerts.json"

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", "AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,JPM,INTC,AMD").split(",")

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
sent_hashes = deque(maxlen=1000)
sent_hashes_lock = threading.Lock()

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route('/dashboard')
def dashboard():
    return send_from_directory("static", "dashboard.html")

# === Utilities ===
def analyze_sentiment(text: str) -> float:
    pos_words = ['gain', 'strong', 'surge', 'up', 'beat', 'rise', 'bull']
    neg_words = ['fall', 'drop', 'miss', 'down', 'loss', 'weak', 'bear']
    text = text.lower()
    pos_score = sum(text.count(w) for w in pos_words)
    neg_score = sum(text.count(w) for w in neg_words)
    return 0.6 if pos_score > neg_score else -0.6 if neg_score > pos_score else 0.0

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=6))
def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            r = requests.post(url, data=data)
            r.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id}")
        except Exception as e:
            logger.error(f"❌ Telegram send failed: {e}")

def log_alert(alert: Dict):
    try:
        if os.path.exists(alerts_file):
            with open(alerts_file, 'r') as f:
                data = json.load(f)
        else:
            data = []
        data.insert(0, alert)
        with open(alerts_file, 'w') as f:
            json.dump(data[:50], f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to log alert: {e}")

def get_price_yfinance(ticker: str) -> Optional[float]:
    try:
        data = yf.Ticker(ticker).history(period='1d')
        return data['Close'].iloc[-1]
    except Exception as e:
        logger.error(f"{ticker}: Failed to get Yahoo price - {e}")
        return None

def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        stock = yf.Ticker(ticker)
        price = get_price_yfinance(ticker)
        if not price:
            return None, None
        options = stock.option_chain(stock.options[0]).calls
        options['diff'] = abs(options['strike'] - price)
        best = options.sort_values('diff').iloc[0]
        return best['strike'], best['lastPrice']
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

def match_ticker(text: str) -> List[str]:
    return [t for t in ticker_list if t in text.upper()]

def fetch_marketaux_news():
    try:
        url = f"https://api.marketaux.com/v1/news/all?language=en&filter_entities=true&api_token={MARKETAUX_API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])[:25]
    except Exception as e:
        logger.error(f"Marketaux fetch failed: {e}")
        return []

def process_news():
    logger.info("📡 Scanning Marketaux...")
    news_items = fetch_marketaux_news()
    alerts_sent = 0

    for article in news_items:
        title = article.get("title", "")
        summary = article.get("description", "")
        content = f"{title} {summary}"

        h = hashlib.sha256(content.encode()).hexdigest()
        with sent_hashes_lock:
            if h in sent_hashes:
                continue
            sent_hashes.append(h)

        sentiment = analyze_sentiment(content)
        if abs(sentiment) < SENTIMENT_THRESHOLD:
            continue

        matched = match_ticker(content)
        for ticker in matched:
            strike, price = get_option_data(ticker)
            if not strike or not price:
                logger.warning(f"Missing option data for {ticker}. Skipping alert.")
                continue

            direction = "Bullish" if sentiment > 0 else "Bearish"
            msg = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {title}
🔄 {direction}
📡 Marketaux

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {strike}
• Expiration: 2 weeks
• Est. Price: ${price:.2f}
• Reason: Sentiment score {sentiment:.2f}
"""
            send_telegram_alert(msg)
            log_alert({"ticker": ticker, "headline": title, "time": datetime.now().isoformat()})
            alerts_sent += 1
            if alerts_sent >= 2:
                return  # limit alerts per scan

# === Routes ===
@app.route("/test/mock_alert")
def mock_alert():
    ticker = "AAPL"
    title = "Apple unveils new AI-powered chip"
    sentiment = 0.7
    strike, price = get_option_data(ticker)
    if not strike or not price:
        return {"status": "Failed", "reason": "Option data missing"}

    msg = f"""
🧪 *Mock Alert*
📰 {title}
🔄 Bullish
🎯 *Trade*: Long Call on {ticker} @ {strike}, est. ${price:.2f}
"""
    send_telegram_alert(msg)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": title}

# === Main Entry ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(process_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("✅ Scheduler started.")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
