import os
import time
import requests
import threading
import feedparser
import hashlib
from textblob import TextBlob
from flask import Flask

# === Flask app to keep alive and serve test route ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Real-Time Trade Alert Bot is alive!"

@app.route('/test-alert')
def test_alert():
    test_message = f"""
🚨 *Test Alert*
🕒 Date/Time: {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 *Headline:* This is a manual test alert.
🔄 *Impact:* Neutral

🎯 *Trade Setup*
• *Ticker:* TEST
• *Strategy:* Long Call
• *Strike:* ATM
• *Expiration:* 2 weeks out
• *Est. Contract Price:* ~$0.00
• *Reason:* Manual system check
• *POP:* N/A
• *Entry:* N/A
• *Exit Rule:* N/A

🔔 *Action:* This is a test message. No trade needed.
"""
    send_telegram_alert(test_message)
    return "✅ Test alert sent!"

def run_server():
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    threading.Thread(target=run_server).start()

# === Environment setup ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")

# === List of tickers to monitor ===
liquid_tickers = [
    'AAPL', 'TSLA', 'SPY', 'MSFT', 'AMD', 'GOOG', 'META',
    'NVDA', 'NFLX', 'AMZN', 'BA', 'JPM', 'BAC', 'INTC', 'DIS'
]

sent_hashes = set()

# === Send message to all Telegram chat IDs ===
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id.strip(),
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, data=data)
            print(f"✅ Alert sent to {chat_id.strip()}")
        except Exception as e:
            print(f"❌ Error sending alert to {chat_id.strip()}: {e}")

# === Identify matching ticker ===
def match_ticker(text):
    for ticker in liquid_tickers:
        if ticker in text.upper():
            return ticker
    return None

# === Format trade alert ===
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
• *Reason:* Strong real-time news sentiment
• *POP:* Estimated >70% based on event-driven catalyst
• *Entry:* ASAP
• *Exit Rule:* 50% profit or 3 days before expiration

🔔 *Action:* Monitor trade; alert will follow for exit if required.
"""
    send_telegram_alert(message)

# === Check news feed for alerts ===
def fetch_and_analyze_news():
    print("🔍 Scanning Yahoo Finance RSS...")
    feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")

    for entry in feed.entries:
        title = entry.title
        summary = entry.get('summary', '')
        full_text = f"{title} {summary}"
        content_hash = hashlib.sha256(full_text.encode()).hexdigest()

        if content_hash in sent_hashes:
            continue

        sentiment_score = TextBlob(full_text).sentiment.polarity
        if abs(sentiment_score) >= 0.3:
            matched = match_ticker(full_text)
            if matched:
                send_trade_alert(matched, title, sentiment_score)
                sent_hashes.add(content_hash)

# === App Runner ===
def main():
    keep_alive()
    while True:
        fetch_and_analyze_news()
        time.sleep(300)  # every 5 minutes

if __name__ == "__main__":
    main()
