import os
import time
import requests
import feedparser
import hashlib
import logging
import re
from collections import deque
from datetime import datetime, timedelta
from flask import Flask, Response
from apscheduler.schedulers.background import BackgroundScheduler
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from bs4 import BeautifulSoup

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route('/health')
def health():
    return {"status": "healthy", "last_scan": getattr(app, 'last_scan_time', 'never')}

@app.route('/test-alert')
def test_alert():
    send_telegram_alert("🚀 Test alert: bot is online.")
    return "Test alert sent."  

@app.route('/scan-now')
def scan_now():
    fetch_and_analyze_news()
    return Response("Scanned", mimetype="text/plain")

# === Environment Variables ===
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS          = os.getenv("TELEGRAM_CHAT_IDS","").split(",")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY")
TRADIER_TOKEN     = os.getenv("TRADIER_API_KEY")
TRADIER_ACCOUNT   = os.getenv("TRADIER_ACCOUNT")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", "0.5"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
LIQUID_TICKERS    = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')

# Validate required variables
required = {
    'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
    'TELEGRAM_CHAT_IDS': CHAT_IDS,
    'FINNHUB_API_KEY': FINNHUB_API_KEY,
    'TRADIER_API_KEY': TRADIER_TOKEN,
    'TRADIER_ACCOUNT': TRADIER_ACCOUNT
}
missing = [k for k,v in required.items() if not v]
if missing:
    logger.error(f"Missing environment variables: {missing}")
    raise SystemExit(1)

# === Sentiment Analyzer ===
sentiment_analyzer = SentimentIntensityAnalyzer()

# === Thread-Safe Hash Tracking ===
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}

# === Tradier Helpers ===
def tradier_headers():
    return {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json"
    }

def tradier_url(path: str) -> str:
    return f"https://api.tradier.com/v1{path}"

def place_option_order(symbol: str, qty: int, strike: float, expiration: str,
                       side: str = "buy", option_type: str = "call") -> Optional[dict]:
    endpoint = f"/accounts/{TRADIER_ACCOUNT}/orders"
    payload = {
        "class": "option",
        "symbol": symbol.upper(),
        "quantity": qty,
        "type": "limit",
        "side": side,
        "duration": "day",
        "price": strike,  # using strike as placeholder price
        "option_type": option_type,
        "strike": strike,
        "expiry": expiration
    }
    try:
        resp = requests.post(tradier_url(endpoint), headers=tradier_headers(), data=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Tradier order failed: {e}")
        return None

# === Telegram Alert Function ===
def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            data = {"chat_id": chat_id.strip(), "text": message, "parse_mode": "Markdown"}
            resp = requests.post(url, data=data, timeout=10)
            resp.raise_for_status()
            logger.info(f"Alert sent to {chat_id}")
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

# === Match Tickers ===
def match_ticker(text: str):
    return [t for t in LIQUID_TICKERS if re.search(rf"\b{t}\b", text.upper())]

# === Fetch Full Article Content ===
def get_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        return ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
    except Exception:
        return ''

# === Fetch and Analyze News ===
def fetch_and_analyze_news():
    try:
        logger.info("Starting news fetch...")
        sources = [
            {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex"},
            {"type": "finnhub", "url": "https://finnhub.io/api/v1/news"}
        ]
        for src in sources:
            articles = []
            if src["type"] == 'rss':
                feed = feedparser.parse(src['url'])
                for entry in feed.entries:
                    title = entry.title
                    summary = entry.get('summary', '')
                    link = entry.get('link', '')
                    content = f"{title} {summary} {get_full_article(link)}"
                    articles.append({'title': title, 'content': content})
            else:
                for ticker in LIQUID_TICKERS:
                    r = requests.get(f"{src['url']}?symbol={ticker}&token={FINNHUB_API_KEY}", timeout=10)
                    r.raise_for_status()
                    for item in r.json():
                        title = item.get('headline', '')
                        summary = item.get('summary', '')
                        content = f"{title} {summary}"
                        articles.append({'title': title, 'content': content})

            for art in articles:
                h = hashlib.sha256(art['content'].encode()).hexdigest()
                if h in sent_hashes:
                    continue
                sent_hashes.append(h)

                score = sentiment_analyzer.polarity_scores(art['content'][:512])['compound']
                if abs(score) >= SENTIMENT_THRESHOLD:
                    symbols = match_ticker(art['content'])
                    for sym in symbols:
                        exp = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')
                        order_resp = place_option_order(sym, 1, 0.0, exp)
                        logger.info(f"Order response: {order_resp}")
                        msg = f"🚨 {sym} | {art['title']}\nSentiment: {score:.2f}"  
                        send_telegram_alert(msg)

        app.last_scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Error in fetch_and_analyze_news: {e}")

# === Main Runner ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    port = int(os.getenv('PORT', '8080'))
    from waitress import serve
    serve(app, host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
