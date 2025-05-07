import os
import time
import threading
import logging
import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
import feedparser
import yfinance as yf
from bs4 import BeautifulSoup
import requests

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
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
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA').split(',')

if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is missing")
    raise ValueError("Missing TELEGRAM_BOT_TOKEN")

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = set()
sentiment_scores: Dict[str, List[float]] = {t: [] for t in ticker_list}

# === News Sources ===
news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"}
]

# === Telegram Alert ===
def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Alert sent to {chat_id}: {message[:80]}")
        except Exception as e:
            logger.error(f"Telegram alert failed for {chat_id}: {e}")

# === Sentiment Analysis ===
def analyze_sentiment(text: str) -> float:
    positive_words = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    negative_words = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    text = text.lower()
    pos = sum(text.count(w) for w in positive_words)
    neg = sum(text.count(w) for w in negative_words)
    return 0.5 if pos > neg else -0.5 if neg > pos else 0.0

# === Yahoo Finance Option Chain ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        stock = yf.Ticker(ticker)
        price = stock.info.get("regularMarketPrice", 0)
        expirations = stock.options
        if not expirations:
            return None, None
        chain = stock.option_chain(expirations[0]).calls
        chain["diff"] = abs(chain["strike"] - price)
        closest = chain.loc[chain["diff"].idxmin()]
        return closest["strike"], closest["lastPrice"]
    except Exception as e:
        logger.error(f"Option fetch failed for {ticker}: {e}")
        return None, None

# === Article Scraping ===
def get_full_article(url: str) -> str:
    try:
        res = requests.get(url, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        return ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
    except Exception as e:
        logger.warning(f"Article scraping failed: {e}")
        return ""

# === Alert Logic ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        logger.warning(f"Missing option data for {ticker}. Skipping alert.")
        return
    msg = f"""
🚨 *Market News Alert*
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')} (UTC)
📰 {headline}
🔄 {direction}
📡 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if sentiment > 0 else 'Put'}
• Strike: {strike}
• Est. Price: ${price:.2f}
• Entry: ASAP | Exit: 50% gain or 3 days pre-expiration
"""
    send_telegram_alert(msg)

# === Core News Scanner ===
def fetch_news():
    for src in news_sources:
        logger.info(f"Scanning {src['name']}...")
        feed = feedparser.parse(src['url'])
        for entry in feed.entries:
            content = f"{entry.title} {entry.get('summary', '')} {get_full_article(entry.get('link', ''))}"
            digest = hashlib.sha256(content.encode()).hexdigest()
            if digest in sent_hashes:
                continue
            sent_hashes.add(digest)
            sentiment = analyze_sentiment(content)
            if abs(sentiment) >= SENTIMENT_THRESHOLD:
                for t in ticker_list:
                    if re.search(rf'\b{t}\b', content.upper()):
                        send_trade_alert(t, entry.title, sentiment, src['name'])
                        sentiment_scores[t].append(sentiment)

# === Daily Summary ===
def send_daily_summary():
    summary = "📊 *Daily Sentiment Summary*\n\n"
    for t, scores in sentiment_scores.items():
        avg = sum(scores) / len(scores) if scores else 0
        summary += f"{t}: {avg:.2f}\n"
        sentiment_scores[t] = []
    send_telegram_alert(summary)

# === Scheduler + Routes ===
@app.route('/test/mock_alert')
def trigger_mock():
    send_trade_alert("AAPL", "Apple announces breakthrough in AI", 0.6, "MockSource")
    return {"status": "Mock alert sent", "ticker": "AAPL"}

def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.add_job(send_daily_summary, 'cron', hour=9, minute=0)
    scheduler.start()
    fetch_news()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    main()
