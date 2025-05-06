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
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup
from transformers import pipeline

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route("/health")
def health():
    return jsonify(status="healthy", last_scan=getattr(app, "last_scan_time", "never"))

# === Tradier Configuration ===
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")
if not TRADIER_API_KEY:
    raise RuntimeError("Missing TRADIER_API_KEY environment variable")

TRADIER_HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

def get_tradier_quote(ticker: str) -> float:
    """Fetch the last trade price for the given ticker from Tradier."""
    url = f"https://api.tradier.com/v1/markets/quotes?symbols={ticker}"
    resp = requests.get(url, headers=TRADIER_HEADERS, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    quote = data["quotes"]["quote"]
    # sometimes a list, sometimes a single dict
    if isinstance(quote, list):
        quote = quote[0]
    return float(quote["last"])

@app.route("/price/<ticker>")
def price(ticker):
    """Return the latest price for a given ticker."""
    try:
        last = get_tradier_quote(ticker.upper())
        return jsonify(ticker=ticker.upper(), last=last)
    except Exception as e:
        logger.error(f"Error fetching price for {ticker}: {e}")
        return jsonify(error=str(e)), 500

# === Environment Variables & Settings ===
BOT_TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS            = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS      = os.getenv("LIQUID_TICKERS", "AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS").split(",")

if not BOT_TOKEN or not CHAT_IDS or not FINNHUB_API_KEY:
    logger.error("Missing one of: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, FINNHUB_API_KEY")
    raise RuntimeError("Missing required environment variables")

# === In-memory State ===
sent_hashes = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock = threading.Lock()

# === Sentiment Analyzer (Lazy) ===
sentiment_analyzer = None
def init_sentiment_analyzer():
    global sentiment_analyzer
    if sentiment_analyzer is None:
        logger.info("Loading sentiment model…")
        sentiment_analyzer = pipeline(
            "sentiment-analysis",
            model="distilbert-base-uncased-finetuned-sst-2-english"
        )

# === Telegram Alert Function ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message: str):
    for chat_id in CHAT_IDS:
        chat_id = chat_id.strip()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message[:4096],
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, data=payload, timeout=5)
        try:
            resp.raise_for_status()
            logger.info(f"✅ Alert sent to chat {chat_id}")
        except Exception as e:
            error = resp.json().get("description", resp.text)
            logger.error(f"❌ Telegram API HTTPError for chat {chat_id}: {error}")
            raise

# === Helpers ===
def match_ticker(text: str):
    text = text.upper()
    return [t for t in LIQUID_TICKERS if re.search(rf"\b{re.escape(t)}\b", text)]

def get_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        ps = soup.find_all("p")
        return " ".join(p.get_text(strip=True) for p in ps)
    except Exception as e:
        logger.warning(f"Error fetching article {url}: {e}")
        return ""

# === Market-News Scan & Alerting ===
news_sources = [
    {"type": "rss",     "url": "https://finance.yahoo.com/news/rssindex", "name": "Yahoo Finance"},
    {"type": "finnhub", "url": "https://finnhub.io/api/v1/news",        "name": "Finnhub"}
]

def fetch_and_analyze_news():
    try:
        init_sentiment_analyzer()
        for src in news_sources:
            logger.info(f"Scanning {src['name']}…")
            articles = []

            if src["type"] == "rss":
                feed = feedparser.parse(src["url"])
                for e in feed.entries:
                    pub = e.get("published") or e.get("updated")
                    if pub:
                        try:
                            pt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
                            if datetime.now(pt.tzinfo) - pt > timedelta(hours=24):
                                continue
                        except:
                            pass
                    title = e.title
                    summary = e.get("summary", "")
                    url = e.get("link", "")
                    body = get_full_article(url)
                    articles.append({
                        "title": title,
                        "content": f"{title} {summary} {body}",
                        "source": src["name"]
                    })

            elif src["type"] == "finnhub":
                for ticker in LIQUID_TICKERS:
                    u = f"{src['url']}?symbol={ticker}&token={FINNHUB_API_KEY}"
                    r = requests.get(u, timeout=5)
                    r.raise_for_status()
                    for item in r.json():
                        dt = datetime.fromtimestamp(item.get("datetime", 0))
                        if datetime.now() - dt > timedelta(hours=24):
                            continue
                        articles.append({
                            "title": item.get("headline", ""),
                            "content": f"{item.get('headline','')} {item.get('summary','')}",
                            "source": src["name"]
                        })

            # Process each article
            for art in articles:
                content = art["content"]
                h = hashlib.sha256(content.encode()).hexdigest()

                with sent_hashes_lock:
                    cutoff = datetime.now() - timedelta(hours=24)
                    # prune old
                    sent_hashes_timestamps.update({
                        k: v for k, v in sent_hashes_timestamps.items() if v > cutoff
                    })
                    if h in sent_hashes:
                        continue
                    sent_hashes.append(h)
                    sent_hashes_timestamps[h] = datetime.now()

                # sentiment
                res = sentiment_analyzer(content[:512])[0]
                score = 0.5 if res["label"] == "POSITIVE" else -0.5
                if abs(score) < SENTIMENT_THRESHOLD:
                    continue

                # tickers
                ts = match_ticker(content)
                for t in ts:
                    # get live price from Tradier
                    price = get_tradier_quote(t)
                    direction = "Bullish" if score > 0 else "Bearish"
                    msg = (
                        f"🚨 *Market News Alert*\n"
                        f"🕒 {time.strftime('%Y-%m-%d %H:%M')} (UTC)\n"
                        f"📰 {art['title']}\n"
                        f"🔄 *{direction}* at `${price:.2f}`\n"
                        f"📡 {art['source']}\n\n"
                        f"🎯 *Trade Setup*\n"
                        f"• Ticker: `{t}`\n"
                        f"• Strategy: Long {'Call' if direction=='Bullish' else 'Put'}\n"
                        f"• Entry Price: `${price:.2f}`\n"
                    )
                    send_telegram_alert(msg)

        app.last_scan_time = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error(f"Error in fetch_and_analyze_news: {e}")

@app.route("/scan-now")
def scan_now():
    fetch_and_analyze_news()
    return jsonify(status="completed", last_scan=app.last_scan_time)

# === Server Keep-Alive & Scheduler ===
def run_server():
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Serving on http://0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port)

def main():
    # start web server
    threading.Thread(target=run_server, daemon=True).start()
    # schedule news scans
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("RealTimeTradeBot started")
    # keep alive
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
