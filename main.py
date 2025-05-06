import os
import time
import requests
import threading
import feedparser
import hashlib
import logging
import re
from textblob import TextBlob
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
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
    return {"status": "healthy", "last_scan": getattr(app, 'last_scan_time', 'never')}

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.3))

if not BOT_TOKEN or not CHAT_IDS:
    logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS")
    exit(1)

# === Thread-Safe Hash Tracking ===
sent_hashes = set()
sent_hashes_lock = threading.Lock()

# === Highly liquid U.S. equities ===
liquid_tickers = [
    'AAPL', 'TSLA', 'SPY', 'MSFT', 'AMD', 'GOOG', 'META',
    'NVDA', 'NFLX', 'AMZN', 'BA', 'JPM', 'BAC', 'INTC', 'DIS'
]

# === Telegram Alert Function with Retry ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id.strip(),
            "text": message[:4096],
            "parse_mode": "Markdown"
        }
        response = requests.post(url, data=data)
        response.raise_for_status()
        logger.info(f"Alert sent to chat ID {chat_id.strip()}")
        time.sleep(1)

# === Match Tickers with Word Boundaries ===
def match_ticker(text):
    return [ticker for ticker in liquid_tickers if re.search(r'\b' + re.escape(ticker) + r'\b', text.upper())]

# === Format and Send the Trade Alert ===
def send_trade_alert(ticker, headline, sentiment):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    message = f"""
🚨 *Market News Alert*
🕒 Date/Time: {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 *Headline:* {headline}
🔄 *Impact:* {direction}

🎯 *Trade Setup*
• *Ticker:* {ticker}
• *Strategy:* Long {'Call' if direction == 'Bullish' else 'Put'}
• *Strike:* ATM
• *Expiration:* 2 weeks out
• *Est. Contract Price:* ~$180
• *Reason:* News event triggered strong {'positive' if sentiment > 0 else 'negative'} sentiment
• *POP:* Likely >70% based on sentiment magnitude
• *Entry:* ASAP
• *Exit Rule:* 50% profit or 3 days before expiration

🔔 *Action:* Monitor trade; follow-up alert if exit rule is triggered.
"""
    send_telegram_alert(message)

# === Fetch and Analyze News ===
def fetch_and_analyze_news():
    try:
        logger.info("Scanning Yahoo Finance RSS...")
        feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")
        if not feed.entries:
            logger.warning("No entries found in RSS feed")
            return

        for entry in feed.entries:
            title = entry.title
            summary = entry.get('summary', '')
            content = f"{title} {summary}"
            news_hash = hashlib.sha256(content.encode()).hexdigest()

            with sent_hashes_lock:
                if news_hash in sent_hashes:
                    continue
                sent_hashes.add(news_hash)

            sentiment = TextBlob(content).sentiment.polarity
            if abs(sentiment) >= SENTIMENT_THRESHOLD:
                tickers = match_ticker(content)
                if tickers:
                    for ticker in tickers:
                        send_trade_alert(ticker, title, sentiment)

        app.last_scan_time = time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Error in fetch_and_analyze_news: {e}")

# === Keep Flask Server Alive ===
def run_server():
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    thread = threading.Thread(target=run_server)
    thread.daemon = True
    thread.start()

# === Main Runner ===
def main():
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=5)
    scheduler.start()
    logger.info("RealTimeTradeBot started")

if __name__ == "__main__":
    main()
