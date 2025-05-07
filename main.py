import os
import time
import requests
import threading
import logging
import feedparser
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask App Setup ===
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
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN').split(',')

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id}")
        except Exception as e:
            logger.error(f"❌ Failed to send alert to {chat_id}: {e}")

# === Marketaux News Fetch ===
def fetch_news_from_marketaux():
    try:
        tickers = ','.join(LIQUID_TICKERS)
        url = f"https://api.marketaux.com/v1/news/all?symbols={tickers}&filter_entities=true&language=en&api_token={MARKETAUX_API_KEY}"
        res = requests.get(url)
        res.raise_for_status()
        articles = res.json().get("data", [])
        logger.info(f"✅ Marketaux returned {len(articles)} articles.")
        return articles
    except Exception as e:
        logger.error(f"❌ Marketaux news fetch failed: {e}")
        return []

# === Mock Alert Test ===
@app.route('/test/mock_alert')
def test_mock():
    message = "\n".join([
        "🚨 *Market News Alert*",
        f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "📰 Apple announces breakthrough in AI technology",
        "🔄 Bullish",
        "📡 MockSource",
        "",
        "🎯 *Trade Setup*",
        "• Ticker: AAPL",
        "• Strategy: Long Call",
        "• Strike: 180",
        "• Expiration: 2 weeks",
        "• Est. Price: $3.50",
        "• Reason: Positive sentiment",
        "• Entry: ASAP",
        "• Exit: 50% profit or 3 days before expiration"
    ])
    send_telegram_alert(message)
    return {"status": "Mock alert sent", "ticker": "AAPL", "headline": "Apple announces breakthrough in AI technology"}

# === Scheduler Setup ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_news_from_marketaux, 'interval', minutes=15)
    scheduler.start()
    logger.info("✅ Scheduler started.")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    main()
