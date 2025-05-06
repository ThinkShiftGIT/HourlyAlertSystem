import os
import time
import requests
import threading
import feedparser
from textblob import TextBlob
from flask import Flask
from datetime import datetime, timedelta

# === Flask app to keep Replit alive ===
app = Flask(__name__)


@app.route('/')
def home():
    return "Bot is running"


def run_server():
    app.run(host='0.0.0.0', port=8080)


def keep_alive():
    t = threading.Thread(target=run_server)
    t.start()


# === Secrets from Replit Environment ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# === Multiple Telegram Users ===
CHAT_IDS = [
    '1654552128',  # Add more Telegram user chat IDs here
    # 'another_chat_id',
]

# === Track high liquidity tickers ===
liquid_tickers = [
    'AAPL', 'TSLA', 'SPY', 'MSFT', 'AMD', 'NVDA', 'GOOG', 'META', 'NFLX',
    'DIS', 'BABA', 'INTC', 'BA', 'NKE', 'CRM'
]

# === Avoid duplicate alerts ===
alerted_titles = set()


# === Telegram Alert Function ===
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, data=data)
            response.raise_for_status()
            print(f"✅ Alert sent to {chat_id}")
        except Exception as e:
            print(f"❌ Error sending alert to {chat_id}: {e}")


# === Trade Alert Formatter ===
def send_trade_alert(ticker, headline, direction):
    message = f"""
🚨 *Market News Alert*
🕒 Date/Time: {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 *News:* {headline}
🔄 *Impact:* {direction}

🎯 *Trade Setup*
• *Ticker:* {ticker}
• *Strategy:* Long {'Call' if direction == 'Bullish' else 'Put'}
• *Legs:*
   – Buy 1× {'Call' if direction == 'Bullish' else 'Put'} @ ITM strike (Exp in 2 weeks)
• *Reason:* {direction} sentiment or earnings catalyst
• *POP:* Estimated >70%
• *Max Risk:* $200
• *Entry:* ASAP
• *Exit Rule:* 50% profit or before expiration

🔔 *Next Steps:* Monitor position; exit-alert will follow if thresholds hit.
"""
    send_telegram_alert(message)


# === Ticker Matcher ===
def match_ticker(text):
    for ticker in liquid_tickers:
        if ticker in text.upper():
            return ticker
    return None


# === Yahoo Finance News Sentiment Scan ===
def fetch_and_analyze_news():
    print("🔍 Checking news...")
    rss_url = "https://finance.yahoo.com/news/rssindex"
    feed = feedparser.parse(rss_url)

    for entry in feed.entries:
        title = entry.title
        summary = entry.get('summary', '')
        combined = f"{title} {summary}"

        if title in alerted_titles:
            continue

        sentiment = TextBlob(combined).sentiment.polarity
        print(f"📰 Title: {title}")
        print(f"🧠 Sentiment Score: {sentiment:.3f}")

        if abs(sentiment) > 0.2:
            matched_ticker = match_ticker(combined)
            if matched_ticker:
                direction = "Bullish" if sentiment > 0 else "Bearish"
                send_trade_alert(matched_ticker, title, direction)
                alerted_titles.add(title)
                break  # only one alert per cycle


# === Earnings Calendar Scan (Finnhub) ===
def fetch_earnings_alerts():
    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={today}&to={tomorrow}&token={FINNHUB_API_KEY}"

    try:
        response = requests.get(url)
        data = response.json()
        earnings = data.get("earningsCalendar", [])
        for report in earnings:
            ticker = report.get("symbol")
            if ticker in liquid_tickers and ticker not in alerted_titles:
                alert = f"{ticker} has earnings on {report.get('date')} (before/after market)"
                send_trade_alert(ticker, alert, "Mixed")
                alerted_titles.add(ticker)
    except Exception as e:
        print(f"❌ Error fetching earnings: {e}")


# === Main Loop ===
def main():
    keep_alive()
    while True:
        fetch_earnings_alerts()
        fetch_and_analyze_news()
        time.sleep(600)  # Check every 10 minutes


if __name__ == "__main__":
    main()
