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
from typing import Dict, Tuple, Optional
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup
from transformers import pipeline
from prometheus_client import Counter, start_http_server
import sentry_sdk

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Sentry Setup (Optional) ===
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=1.0)
    logger.info("Sentry initialized")

# === Flask App Setup ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "last_scan": getattr(app, 'last_scan_time', 'never')}

# === Prometheus Metrics ===
alerts_sent = Counter('tradebot_alerts_sent', 'Number of Telegram alerts sent')
articles_processed = Counter('tradebot_articles_processed', 'Number of news articles processed')
errors = Counter('tradebot_errors', 'Number of errors encountered')

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

# Validate required environment variables
if not BOT_TOKEN:
    msg = "Missing TELEGRAM_BOT_TOKEN environment variable"
    logger.error(msg)
    raise ValueError(msg)
if not CHAT_IDS:
    msg = "Missing TELEGRAM_CHAT_IDS environment variable"
    logger.error(msg)
    raise ValueError(msg)
if not FINNHUB_API_KEY:
    msg = "Missing FINNHUB_API_KEY environment variable"
    logger.error(msg)
    raise ValueError(msg)

# === Thread-Safe Hash Tracking ===
sent_hashes = deque(maxlen=10000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()

# === Option Cache ===
option_cache: Dict[str, Tuple[Optional[int], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
option_cache_lock = threading.Lock()

# === News Sources ===
news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "rss", "url": "https://feeds.reuters.com/reuters/businessNews", "name": "Reuters"},
    {"type": "finnhub", "url": "https://finnhub.io/api/v1/news", "name": "Finnhub"}
]

# === FinBERT Sentiment Analysis ===
try:
    sentiment_analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")
except Exception as e:
    logger.error(f"Failed to load FinBERT: {e}")
    raise RuntimeError(f"Failed to load FinBERT: {e}")

# === Telegram Alert Function ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Alert sent to chat ID {chat_id.strip()}")
            alerts_sent.inc()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Failed to send alert to {chat_id.strip()}: {e}, Response: {response.text if 'response' in locals() else 'N/A'}")
            errors.inc()
            raise

# === Match Tickers ===
def match_ticker(text):
    return [ticker for ticker in LIQUID_TICKERS if re.search(r'\b' + re.escape(ticker) + r'\b', text.upper())]

# === Fetch Full Article Content ===
def get_full_article(url):
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        return ' '.join(p.get_text(strip=True) for p in paragraphs) if paragraphs else ""
    except Exception as e:
        logger.error(f"Error fetching article {url}: {e}")
        errors.inc()
        return ""

# === Fetch Option Data from Finnhub ===
def get_option_data(ticker):
    with option_cache_lock:
        # Check cache
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]

    try:
        # Get current stock price
        quote_url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
        quote = requests.get(quote_url).json()
        current_price = quote.get('c', 0)
        if not current_price:
            raise ValueError("No current price available")

        # Get option chain (2 weeks out, assuming 14 days)
        expiration_date = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')
        option_url = f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}"
        option_data = requests.get(option_url).json()
        
        # Find ATM strike
        atm_strike = None
        option_price = None
        min_diff = float('inf')
        for contract in option_data.get('data', []):
            for option in contract.get('options', {}).get('CALL', []):
                strike = option['strike']
                diff = abs(strike - current_price)
                if diff < min_diff:
                    min_diff = diff
                    atm_strike = strike
                    option_price = option.get('lastPrice', 0) or option.get('ask', 0) or 0

        result = (atm_strike, option_price) if atm_strike else (None, None)
        
        # Update cache
        with option_cache_lock:
            option_cache[ticker] = result
            option_cache_timestamps[ticker] = datetime.now()
        
        return result
    except Exception as e:
        logger.error(f"Error fetching option data for {ticker}: {e}")
        errors.inc()
        return None, None

# === Send Trade Alert ===
def send_trade_alert(ticker, headline, sentiment, source_name):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    atm_strike, option_price = get_option_data(ticker)
    if atm_strike is None or option_price is None:
        logger.warning(f"Skipping alert for {ticker} due to missing option data")
        return

    message = f"""
🚨 *Market News Alert*
🕒 Date/Time: {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 *Headline:* {headline}
🔄 *Impact:* {direction}
📡 *Source:* {source_name}

🎯 *Trade Setup*
• *Ticker:* {ticker}
• *Strategy:* Long {'Call' if direction == 'Bullish' else 'Put'}
• *Strike:* {atm_strike}
• *Expiration:* 2 weeks out
• *Est. Contract Price:* ${option_price:.2f}
• *Reason:* News event with {direction.lower()} sentiment (score: {sentiment:.2f})
• *POP:* Based on historical sentiment-driven moves
• *Entry:* ASAP
• *Exit Rule:* 50% profit or 3 days before expiration

🔔 *Action:* Monitor trade; follow-up alert if exit rule is triggered.
"""
    send_telegram_alert(message)

# === Fetch and Analyze News ===
def fetch_and_analyze_news():
    try:
        for source in news_sources:
            logger.info(f"Scanning {source['name']}...")
            articles = []

            if source['type'] == 'rss':
                feed = feedparser.parse(source['url'])
                if not feed.entries:
                    logger.warning(f"No entries in {source['name']}")
                    continue
                for entry in feed.entries:
                    published = entry.get('published') or entry.get('updated')
                    if published:
                        try:
                            published_time = datetime.strptime(published, '%a, %d %b %Y %H:%M:%S %z')
                            if (datetime.now(published_time.tzinfo) - published_time) > timedelta(hours=24):
                                continue  # Skip old articles
                        except ValueError:
                            pass
                    title = entry.title
                    summary = entry.get('summary', '')
                    article_url = entry.get('link', '')
                    article_content = get_full_article(article_url)
                    articles.append({
                        'title': title,
                        'content': f"{title} {summary} {article_content}",
                        'source': source['name']
                    })

            elif source['type'] == 'finnhub':
                for ticker in LIQUID_TICKERS:
                    url = f"{source['url']}?symbol={ticker}&token={FINNHUB_API_KEY}"
                    response = requests.get(url)
                    response.raise_for_status()
                    news_items = response.json()
                    for item in news_items:
                        published_time = datetime.fromtimestamp(item.get('datetime', 0))
                        if (datetime.now() - published_time) > timedelta(hours=24):
                            continue
                        articles.append({
                            'title': item.get('headline', ''),
                            'content': f"{item.get('headline', '')} {item.get('summary', '')}",
                            'source': source['name']
                        })

            for article in articles:
                articles_processed.inc()
                content = article['content']
                news_hash = hashlib.sha256(content.encode()).hexdigest()

                with sent_hashes_lock:
                    cutoff = datetime.now() - timedelta(hours=24)
                    sent_hashes_timestamps = {k: v for k, v in sent_hashes_timestamps.items() if v > cutoff}
                    if news_hash in sent_hashes:
                        continue
                    sent_hashes.append(news_hash)
                    sent_hashes_timestamps[news_hash] = datetime.now()

                result = sentiment_analyzer(content[:512])  # Truncate for FinBERT
                sentiment = 0.5 if result[0]['label'] == 'positive' else -0.5 if result[0]['label'] == 'negative' else 0
                if abs(sentiment) >= SENTIMENT_THRESHOLD:
                    tickers = match_ticker(content)
                    if tickers:
                        for ticker in tickers:
                            send_trade_alert(ticker, article['title'], sentiment, article['source'])

        app.last_scan_time = time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Error in fetch_and_analyze_news: {e}")
        errors.inc()

# === Keep Flask Server Alive ===
def run_server():
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

# === Monitor Scheduler ===
def monitor_scheduler(scheduler):
    while True:
        if not scheduler.running:
            logger.error("Scheduler stopped, restarting...")
            scheduler.start()
        time.sleep(60)

# === Main Runner ===
def main():
    start_http_server(8000)  # Expose Prometheus metrics
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    threading.Thread(target=monitor_scheduler, args=(scheduler,), daemon=True).start()
    logger.info("RealTimeTradeBot started")

if __name__ == "__main__":
    main()
