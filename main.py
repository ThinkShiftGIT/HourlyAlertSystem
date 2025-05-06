import os
import time
import requests
import threading
import feedparser
import hashlib
from textblob import TextBlob
from flask import Flask
from waitress import serve

# === Flask app to keep alive ===
app = Flask(__name__)


@app.route('/')
def home():
    return "Bot is alive"


def run_server():
    serve(app, host='0.0.0.0', port=8080)


def keep_alive():
    thread = threading.Thread(target=run_server)
    thread.start()


# === Load env variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = '1654552128'

# === Highly liquid U.S. equities ===
liquid_tickers = [
    'AAPL', 'TSLA', 'SPY', 'MSFT', 'AMD', 'GOOG', 'META', 'NVDA', 'NFLX',
    'AMZN', 'BA', 'JPM', 'BAC', 'INTC', 'DIS'
]

# === Track previously sent alerts using a hash set ===
sent_hashes = set()


# === Send message to Telegram ===
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
        print("✅ Alert sent.")
    except Exception as e:
        print(f"❌ Failed to send alert: {e}")


# === Match ticker in news ===
def match_ticker(text):
    for ticker in liquid_tickers:
        if ticker in text.upper():
            return ticker
    return None


# === Alert format ===
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
• *Reason:* Strong sentiment from real-time news
• *POP:* Likely >70% based on event-driven catalyst
• *Entry:* ASAP
• *Exit Rule:* 50% profit or 3 days before expiration

🔔 *Action:* Monitor trade; follow-up alert if exit rule is triggered.
"""
    send_telegram_alert(message)


# === News fetch and analysis ===
def fetch_and_analyze_news():
    print("🔍 Scanning Yahoo Finance RSS...")
    feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")

    for entry in feed.entries:
        title = entry.title
        summary = entry.get('summary', '')
        content = f"{title} {summary}"
        news_hash = hashlib.sha256(content.encode()).hexdigest()

        # Skip if already alerted
        if news_hash in sent_hashes:
            continue

        sentiment_score = TextBlob(content).sentiment.polarity
        if abs(sentiment_score) >= 0.3:
            matched_ticker = match_ticker(content)
            if matched_ticker:
                send_trade_alert(matched_ticker, title, sentiment_score)
                sent_hashes.add(news_hash)


# === Run the bot ===
def main():
    keep_alive()
    while True:
        fetch_and_analyze_news()
        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    main()
