```python
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

# Try importing BeautifulSoup, but make it optional
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logging.warning("BeautifulSoup (bs4) not available. Article scraping will be disabled.")

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
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 1))  # Changed to 1 for testing
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

# === Dynamic Ticker List (In-Memory) ===
ticker_list: List[str] = LIQUID_TICKERS.copy()
ticker_list_lock = threading.Lock()

# === Thread-Safe Hash Tracking ===
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()

# === Option Cache ===
option_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
option_cache_timestamps: Dict[str, datetime] = {}
option_cache_lock = threading.Lock()

# === Sentiment Scores for Daily Summary ===
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}
sentiment_scores_lock = threading.Lock()

# === News Sources ===
news_sources = [
    {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "finnhub", "url": "https://finnhub.io/api/v1/news", "name": "Finnhub"}
]

# === Rule-Based Sentiment Analysis ===
def analyze_sentiment(text: str) -> float:
    """Simple rule-based sentiment analysis using keywords."""
    positive_words = {'growth', 'profit', 'rise', 'up', 'gain', 'strong', 'bullish'}
    negative_words = {'loss', 'decline', 'down', 'drop', 'weak', 'bearish', 'fall'}
    
    text = text.lower()
    pos_count = sum(text.count(word) for word in positive_words)
    neg_count = sum(text.count(word) for word in negative_words)
    
    if pos_count > neg_count:
        return 0.5  # Positive
    elif neg_count > pos_count:
        return -0.5  # Negative
    return 0.0  # Neutral

# === Telegram Alert Function ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message, chat_ids=CHAT_IDS):
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            response = requests.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Alert sent to chat ID {chat_id.strip()}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Failed to send alert to {chat_id.strip()}: {e}")
            raise

# === Ticker Management Functions ===
def verify_symbol(symbol: str) -> bool:
    """Verify if a ticker is valid using Finnhub API."""
    try:
        quote_url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        response = requests.get(quote_url)
        response.raise_for_status()
        data = response.json()
        return 'c' in data and data['c'] != 0
    except Exception as e:
        logger.error(f"Error verifying symbol {symbol}: {e}")
        return False

def add_ticker(symbol: str) -> str:
    """Add a ticker to the in-memory list."""
    with ticker_list_lock:
        if symbol in ticker_list:
            return f"{symbol} already exists in the list."
        if verify_symbol(symbol):
            ticker_list.append(symbol)
            daily_sentiment_scores[symbol] = []
            return f"Added {symbol} to the list."
        return f"{symbol} is not a valid symbol."

def remove_ticker(symbol: str) -> str:
    """Remove a ticker from the in-memory list."""
    with ticker_list_lock:
        if symbol not in ticker_list:
            return f"{symbol} is not in the list."
        ticker_list.remove(symbol)
        daily_sentiment_scores.pop(symbol, None)
        return f"Removed {symbol} from the list."

def list_tickers() -> str:
    """List all tickers."""
    with ticker_list_lock:
        if not ticker_list:
            return "No tickers in the list."
        return "List of tickers:\n" + "\n".join(ticker_list)

# === Match Tickers ===
def match_ticker(text: str) -> List[str]:
    with ticker_list_lock:
        return [ticker for ticker in ticker_list if re.search(r'\b' + re.escape(ticker) + r'\b', text.upper())]

# === Fetch Full Article Content ===
def get_full_article(url: str) -> str:
    if not BS4_AVAILABLE:
        logger.warning(f"Cannot fetch article content from {url}: bs4 not available")
        return ""
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        return ' '.join(p.get_text(strip=True) for p in paragraphs) if paragraphs else ""
    except Exception as e:
        logger.error(f"Error fetching article {url}: {e}")
        return ""

# === Fetch Option Data from Finnhub ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    with option_cache_lock:
        if ticker in option_cache and option_cache_timestamps[ticker] > datetime.now() - timedelta(minutes=15):
            return option_cache[ticker]

    try:
        quote_url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
        quote = requests.get(quote_url).json()
        current_price = quote.get('c', 0)
        if not current_price:
            raise ValueError("No current price available")

        option_url = f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}"
        option_data = requests.get(option_url).json()
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
        with option_cache_lock:
            option_cache[ticker] = result
            option_cache_timestamps[ticker] = datetime.now()
        return result
    except Exception as e:
        logger.error(f"Error fetching option data for {ticker}: {e}")
        return None, None

# === Send Trade Alert ===
def send_trade_alert(ticker: str, headline: str, sentiment: float, source_name: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    atm_strike, option_price = get_option_data(ticker)
    if atm_strike is None or option_price is None:
        logger.warning(f"Skipping alert for {ticker} due to missing option data")
        return

    message = f"""
🚨 *Market News Alert*
🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC-5)
📰 {headline}
🔄 {direction}
📡 {source_name}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if direction == 'Bullish' else 'Put'}
• Strike: {atm_strike}
• Expiration: 2 weeks
• Est. Contract Price: ${option_price:.2f}
• Reason: Sentiment score {sentiment:.2f}
• Entry: ASAP
• Exit: 50% profit or 3 days before expiration
"""
    send_telegram_alert(message)

# === Daily Sentiment Summary ===
def send_daily_summary():
    with sentiment_scores_lock:
        if not daily_sentiment_scores:
            message = "Daily Stock Sentiment Summary:\nNo data available."
        else:
            message = "Daily Stock Sentiment Summary:\n\n"
            for ticker, scores in daily_sentiment_scores.items():
                avg_score = sum(scores) / len(scores) if scores else 0
                message += f"{ticker}: {avg_score:.2f}\n"
            # Reset scores for the next day
            for ticker in daily_sentiment_scores:
                daily_sentiment_scores[ticker] = []
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
                    continue
                for entry in feed.entries:
                    published = entry.get('published') or entry.get('updated')
                    if published:
                        try:
                            published_time = datetime.strptime(published, '%a, %d %b %Y %H:%M:%S %z')
                            if (datetime.now(published_time.tzinfo) - published_time) > timedelta(hours=24):
                                continue
                        except ValueError:
                            pass
                    title = entry.title
                    summary = entry.get('summary', '')
                    article_url = entry.get('link', '')
                    article_content = get_full_article(article_url)
                    articles.append({'title': title, 'content': f"{title} {summary} {article_content}", 'source': source['name']})
            elif source['type'] == 'finnhub':
                with ticker_list_lock:
                    for ticker in ticker_list:
                        url = f"{source['url']}?symbol={ticker}&token={FINNHUB_API_KEY}"
                        response = requests.get(url)
                        response.raise_for_status()
                        news_items = response.json()
                        for item in news_items:
                            if (datetime.now() - datetime.fromtimestamp(item.get('datetime', 0))) > timedelta(hours=24):
                                continue
                            articles.append({'title': item.get('headline', ''), 'content': f"{item.get('headline', '')} {item.get('summary', '')}", 'source': source['name']})

            for article in articles:
                content = article['content']
                news_hash = hashlib.sha256(content.encode()).hexdigest()
                with sent_hashes_lock:
                    cutoff = datetime.now() - timedelta(hours=24)
                    sent_hashes_timestamps = {k: v for k, v in sent_hashes_timestamps.items() if v > cutoff}
                    if news_hash in sent_hashes:
                        continue
                    sent_hashes.append(news_hash)
                    sent_hashes_timestamps[news_hash] = datetime.now()

                sentiment = analyze_sentiment(content)
                if abs(sentiment) >= SENTIMENT_THRESHOLD:
                    tickers = match_ticker(content)
                    if tickers:
                        for ticker in tickers:
                            send_trade_alert(ticker, article['title'], sentiment, article['source'])
                            with sentiment_scores_lock:
                                if ticker in daily_sentiment_scores:
                                    daily_sentiment_scores[ticker].append(sentiment)
    except Exception as e:
        logger.error(f"Error in fetch_and_analyze_news: {e}")

# === Telegram Command Handler ===
@app.route('/telegram/<command>')
def handle_telegram_command(command):
    if command == 'list_tickers':
        message = list_tickers()
    elif command.startswith('add_ticker_'):
        symbol = command.split('_')[2].upper()
        message = add_ticker(symbol)
    elif command.startswith('remove_ticker_'):
        symbol = command.split('_')[2].upper()
        message = remove_ticker(symbol)
    else:
        message = "Unknown command. Available commands:\n/list_tickers\n/add_ticker_<symbol>\n/remove_ticker_<symbol>"
    send_telegram_alert(message)
    return {"status": "command processed", "message": message}

# === Main Runner ===
def main():
    # Start the scheduler in a separate thread
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.add_job(send_daily_summary, 'cron', hour=9, minute=0)
    scheduler.start()
    fetch_and_analyze_news()  # Force an immediate scan
    logger.info("Forced initial news scan")
    # Start the server in the main thread
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting server on port {port}...")
    serve(app, host='0.0.0.0', port=port, threads=2, backlog=128, channel_timeout=60, cleanup_interval=15)

if __name__ == "__main__":
    main()
```

---

### **Changes Made**
1. **Modified `SCAN_INTERVAL_MINUTES`**:
   - Changed from `SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))` to `SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 1))`.
   - This reduces the interval to 1 minute for faster testing. You can revert to 5 after testing by changing it back.

2. **Added Forced News Scan**:
   - Added `fetch_and_analyze_news()` and `logger.info("Forced initial news scan")` after `scheduler.start()` in the `main()` function.
   - This triggers an immediate news scan on startup, generating logs and potential Telegram alerts.

### **Steps to Apply and Test**
1. **Replace `main.py`**:
   - Copy the entire content above into your `main.py` file in the `RealTimeTradeBot` directory on your local machine.

2. **Commit and Push**:
   - Stage and commit the changes:
     ```bash
     git add main.py
     git commit -m "Add forced news scan for testing and set SCAN_INTERVAL_MINUTES to 1"
     git push origin main
     ```

3. **Monitor Redeploy on Render**:
   - Render will automatically redeploy. Check the **Logs** tab in the Render Dashboard for:
     - “Forced initial news scan”
     - “Scanning Yahoo Finance...” or “Scanning Finnhub...”
     - Any errors (e.g., API failures).
   - The redeploy should take a few minutes. The forced scan will run immediately after startup.

4. **Check Telegram**:
   - After the deploy completes (e.g., within 1-2 minutes), check your Telegram chat (ID `1654552128`) for alerts.
   - Expected alerts if news matches a ticker with strong sentiment (e.g., AAPL with score 0.5).

5. **Revert Changes (Optional)**:
   - After testing, revert `SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 1))` to `SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))` and remove the `fetch_and_analyze_news()` call and log.
   - Commit and push:
     ```bash
     git add main.py
     git commit -m "Revert SCAN_INTERVAL_MINUTES to 5 and remove forced scan"
     git push origin main
     ```

### **What to Expect**
- The forced scan should generate logs like “Scanning Yahoo Finance...” and potentially send Telegram alerts.
- If no alerts appear, check logs for errors (e.g., Finnhub API rate limits, Telegram issues).
- The 1-minute interval will ensure subsequent scans occur quickly for further testing.

### **Next Steps**
- Share the updated Render logs or Telegram results after the redeploy.
- If the scheduler still doesn’t log or run, we’ll add more debugging (e.g., `scheduler.get_jobs()` log).

Let me know how it goes!
