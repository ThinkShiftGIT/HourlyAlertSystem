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
from typing import Dict, Tuple, Optional, List
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logging.warning("BeautifulSoup (bs4) not available. Article scraping will be disabled.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

if not BOT_TOKEN or not CHAT_IDS or not FINNHUB_API_KEY:
    raise ValueError("Missing one or more required environment variables.")

logger.info(f"🚀 CHAT_IDS resolved at startup: {CHAT_IDS}")

ticker_list = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
option_cache_lock = threading.Lock()
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "finnhub", "url": "https://finnhub.io/api/v1/news", "name": "Finnhub"}
]

def analyze_sentiment(text: str) -> float:
    positive_words = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    negative_words = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    text = text.lower()
    pos_count = sum(text.count(word) for word in positive_words)
    neg_count = sum(text.count(word) for word in negative_words)
    if pos_count > neg_count:
        return 0.5
    elif neg_count > pos_count:
        return -0.5
    return 0.0

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message, chat_ids=CHAT_IDS):
    if not chat_ids or chat_ids == ['']:
        logger.warning("No valid TELEGRAM_CHAT_IDS found. Skipping alert.")
        return
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            logger.info(f"Sending message to Telegram ID {chat_id.strip()}")
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"✅ Alert sent to chat ID {chat_id.strip()}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Failed to send to {chat_id.strip()}: {e}")
            raise

def verify_symbol(symbol: str) -> bool:
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        data = requests.get(url).json()
        return data.get('c', 0) != 0
    except Exception as e:
        logger.error(f"Verify error for {symbol}: {e}")
        return False

def add_ticker(symbol: str) -> str:
    with ticker_list_lock:
        if symbol in ticker_list:
            return f"{symbol} already exists."
        if verify_symbol(symbol):
            ticker_list.append(symbol)
            daily_sentiment_scores[symbol] = []
            return f"Added {symbol}."
        return f"{symbol} is not valid."

def remove_ticker(symbol: str) -> str:
    with ticker_list_lock:
        if symbol not in ticker_list:
            return f"{symbol} not found."
        ticker_list.remove(symbol)
        daily_sentiment_scores.pop(symbol, None)
        return f"Removed {symbol}."

def list_tickers() -> str:
    with ticker_list_lock:
        return "Tracked tickers:\n" + "\n".join(ticker_list)

def match_ticker(text: str) -> List[str]:
    with ticker_list_lock:
        return [t for t in ticker_list if re.search(r'\\b' + re.escape(t) + r'\\b', text.upper())]

def get_full_article(url: str) -> str:
    if not BS4_AVAILABLE:
        return ""
    try:
        soup = BeautifulSoup(requests.get(url, timeout=5).text, 'html.parser')
        return ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
    except Exception as e:
        logger.warning(f"Article scrape failed: {e}")
        return ""

def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]
    try:
        current_price = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}").json().get('c', 0)
        if not current_price:
            return None, None
        option_data = requests.get(f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}").json()
        atm_strike, option_price, min_diff = None, None, float('inf')
        for contract in option_data.get('data', []):
            for option in contract.get('options', {}).get('CALL', []):
                strike = option['strike']
                diff = abs(strike - current_price)
                if diff < min_diff:
                    atm_strike = strike
                    option_price = option.get('lastPrice', 0) or option.get('ask', 0)
                    min_diff = diff
        with option_cache_lock:
            option_cache[ticker] = (atm_strike, option_price)
            option_cache_timestamps[ticker] = datetime.now()
        return atm_strike, option_price
    except Exception as e:
        logger.error(f"Option fetch failed: {e}")
        return None, None

def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if strike is None or price is None:
        return
    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {headline}
🔄 {direction}
📱 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {strike}
• Expiration: 2 weeks
• Est. Contract Price: ${price:.2f}
• Reason: Sentiment score {sentiment:.2f}
• Entry: ASAP
• Exit: 50% profit or 3 days before expiration
"""
    send_telegram_alert(message)

def send_daily_summary():
    with sentiment_scores_lock:
        message = "📊 *Daily Sentiment Summary*\n\n"
        for t, scores in daily_sentiment_scores.items():
            avg = sum(scores)/len(scores) if scores else 0
            message += f"{t}: {avg:.2f}\n"
            daily_sentiment_scores[t] = []
    send_telegram_alert(message)

def fetch_and_analyze_news():
    try:
        for source in news_sources:
            logger.info(f"Scanning {source['name']}...")
            articles = []
            if source['type'] == 'rss':
                for entry in feedparser.parse(source['url']).entries:
                    title = entry.title
                    content = f"{title} {entry.get('summary', '')} {get_full_article(entry.get('link', ''))}"
                    articles.append({"title": title, "content": content, "source": source['name']})
            elif source['type'] == 'finnhub':
                with ticker_list_lock:
                    for ticker in ticker_list:
                        for item in requests.get(f"{source['url']}?symbol={ticker}&token={FINNHUB_API_KEY}").json():
                            content = f"{item.get('headline', '')} {item.get('summary', '')}"
                            articles.append({"title": item.get('headline', ''), "content": content, "source": source['name']})
            for article in articles:
                h = hashlib.sha256(article['content'].encode()).hexdigest()
                with sent_hashes_lock:
                    if h in sent_hashes:
                        continue
                    sent_hashes.append(h)
                    sent_hashes_timestamps[h] = datetime.now()
                sentiment = analyze_sentiment(article['content'])
                if abs(sentiment) >= SENTIMENT_THRESHOLD:
                    for t in match_ticker(article['content']):
                        send_trade_alert(t, article['title'], sentiment, article['source'])
                        with sentiment_scores_lock:
                            daily_sentiment_scores[t].append(sentiment)
    except Exception as e:
        logger.error(f"Error in news scan: {e}")

@app.route('/telegram/<command>')
def handle_command(command):
    if command == 'list_tickers':
        msg = list_tickers()
    elif command.startswith('add_ticker_'):
        msg = add_ticker(command.split('_')[2].upper())
    elif command.startswith('remove_ticker_'):
        msg = remove_ticker(command.split('_')[2].upper())
    else:
        msg = "Commands:\n/list_tickers\n/add_ticker_<symbol>\n/remove_ticker_<symbol>"
    send_telegram_alert(msg)
    return {"result": msg}

@app.route('/test/mock_alert')
def trigger_mock_alert():
    ticker = "AAPL"
    headline = "Apple announces breakthrough in AI technology"
    sentiment = 0.6
    source = "MockSource"
    send_trade_alert(ticker, headline, sentiment, source)
    return {"status": "Mock alert sent", "ticker": ticker, "headline": headline}

def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.add_job(send_daily_summary, 'cron', hour=9, minute=0)
    scheduler.start()
    fetch_and_analyze_news()
    logger.info("Forced initial scan")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
