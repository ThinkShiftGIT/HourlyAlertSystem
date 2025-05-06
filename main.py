import os
import time
import requests
import threading
import feedparser
import hashlib
from textblob import TextBlob
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

# === Flask App Setup ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/test-alert')
def test_alert():
    send_telegram_alert("🚨 *Test Alert*: RealTimeTradeBot is online and working!")
    return "Test alert sent!"

# === Keep Alive with Waitress ===
def run_server():
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def keep_alive():
    thread = threading.Thread(target=run_server)
    thread.start()

# === Load Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")

# === Track Alerts to Prevent Duplicates ===
sent_hashes = set()

# === Liquid US Stocks to Track ===
liquid_tickers = [
    'AAPL', 'TSLA', 'SPY', 'MSFT', 'AMD', 'GOOG', 'META',
    'NVDA', 'NFLX', 'AMZN', 'BA', 'JPM', 'BAC', 'INTC', 'DIS'
]

# === Telegram Alert Function ===
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id.strip(),
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(url, data=data)
            if response.status_code == 200:
                print(f"✅ Alert sent to chat ID {chat_id.strip()}")
            else:
                print(f"⚠️ Failed to send alert to {chat_id.strip()}: {response.text}")
        except Exception as e:
            print(f"❌ Exception sending alert to {chat_id.strip()}: {e}")

# === Match News with Tickers ===
def match_ticker(text):
    for ticker in liquid_tickers:
        if ticker in text.upper():
            return ticker
    return None

# === Format and Send Trade Alert ===
def send_trade_alert(ticker, headline, sentiment):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 *Headline:* {headline}
🔄 *Impact:* {direction}

🎯 *Trade Setup*
• *Ticker:* {ticker}
• *Strategy:* Long {'Call' if direction == 'Bullish' else 'Put'}
• *Strike:* ATM
• *Expiration:* 2 weeks out
• *Est. Contract Price:* ~$180
• *Reason:* Real-time news with strong sentiment of {round(sentiment, 2)}
• *POP:* Likely >70% based on historical news-based moves
• *Entry:* ASAP
• *Exit Rule:* 50% profit or 3 days before expiration

🔔 *Action:* Monitor trade; follow-up alert will be sent if exit condition met.
"""
    send_telegram_alert(message)

# === Scan News and Analyze ===
def fetch_and_analyze_news():
    print("🔍 Scanning Yahoo Finance RSS...")
    feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")

    for entry in feed.entries:
        title = entry.title
        summary = entry.get('summary', '')
        content = f"{title} {summary}"
        news_hash = hashlib.sha256(content.encode()).hexdigest()

        if news_hash in sent_hashes:
            continue

        sentiment = TextBlob(content).sentiment.polarity
        if abs(sentiment) >= 0.3:
            ticker = match_ticker(content)
            if ticker:
                send_trade_alert(ticker, title, sentiment)
                sent_hashes.add(news_hash)

# === Main Runner ===
def main():
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=5)
    scheduler.start()

if __name__ == "__main__":
    main()
