import os
import time
import requests
import logging
import feedparser
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, Tuple

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__)

# === Environment ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 15))
TICKERS = os.getenv("TICKERS", "AAPL,MSFT,TSLA,GOOG,NVDA,AMZN").split(",")

# === Alert Caching ===
sent_hashes = deque(maxlen=100)

# === Telegram Alert ===
def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured properly.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message[:4096], "parse_mode": "Markdown"}
        r = requests.post(url, data=data)
        r.raise_for_status()
        logger.info("✅ Sent alert to Telegram.")
    except Exception as e:
        logger.error(f"❌ Telegram alert failed: {e}")

# === Polygon Quote Fetch ===
def get_price_polygon(ticker: str) -> Optional[float]:
    try:
        url = f"https://api.polygon.io/v2/last/nbbo/{ticker}?apiKey={POLYGON_API_KEY}"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        return float(data.get("results", {}).get("bid", 0))
    except Exception as e:
        logger.error(f"Polygon quote fetch failed for {ticker}: {e}")
        return None

# === Polygon Option Data ===
def get_option_data_polygon(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?apiKey={POLYGON_API_KEY}"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json().get("results", {}).get("options", [])
        if not data:
            return None, None
        option = data[0]  # Pick top result
        strike = option.get("details", {}).get("strike_price")
        price = option.get("last_quote", {}).get("ask")
        return strike, price
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === Marketaux News Fetch ===
def fetch_marketaux_news() -> list:
    try:
        url = f"https://api.marketaux.com/v1/news/all?api_token={MARKETAUX_API_KEY}&language=en&filter_entities=true"
        r = requests.get(url)
        r.raise_for_status()
        articles = r.json().get("data", [])
        return articles
    except Exception as e:
        logger.error(f"Marketaux news fetch failed: {e}")
        return []

# === Scan Logic ===
def scan_and_alert():
    logger.info("📡 Starting scan cycle...")
    articles = fetch_marketaux_news()
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')}"
        if not content:
            continue
        h = hash(content)
        if h in sent_hashes:
            continue
        sent_hashes.append(h)

        for ticker in TICKERS:
            if ticker in content:
                logger.info(f"🔎 Match found: {ticker} in headline.")
                strike, option_price = get_option_data_polygon(ticker)
                last_price = get_price_polygon(ticker)

                if strike is None or option_price is None or last_price is None:
                    logger.warning(f"⚠️ Skipping alert for {ticker}. Missing option/price data.")
                    continue

                message = f"""
🚨 *Trade Alert: {ticker}*
📰 {article.get('title')}
📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

*Market Price:* ${last_price:.2f}
*Option Strike:* ${strike:.2f}
*Ask Price:* ${option_price:.2f}
*Source:* Marketaux
                """
                send_telegram_alert(message)
                break  # only send 1 alert per article

# === Routes ===
@app.route("/")
def home():
    return "✅ RealTimeTradeBot is running."

@app.route("/health")
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route("/test/mock_alert")
def test_alert():
    msg = "🧪 This is a test alert from RealTimeTradeBot."
    send_telegram_alert(msg)
    return {"result": "Sent"}

# === App Init ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_and_alert, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("⏰ Scheduler started. Beginning first scan immediately...")
    scan_and_alert()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
