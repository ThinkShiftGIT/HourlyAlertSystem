import os
import time
import logging
import threading
import requests
import feedparser
import hashlib
import re

from collections import deque
from datetime import datetime, timedelta
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# === Logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)
app.last_scan = None

# === Environment Variables ===
BOT_TOKEN             = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS_RAW          = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
FINNHUB_API_KEY       = os.getenv("FINNHUB_API_KEY", "").strip()
SENTIMENT_THRESHOLD   = float(os.getenv("SENTIMENT_THRESHOLD", "0.5"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
LIQUID_TICKERS        = os.getenv("LIQUID_TICKERS", "AAPL,TSLA,SPY,MSFT,AMD,GOOG").split(",")

# Validate required env vars
missing = []
if not BOT_TOKEN:       missing.append("TELEGRAM_BOT_TOKEN")
if not CHAT_IDS_RAW:    missing.append("TELEGRAM_CHAT_IDS")
if not FINNHUB_API_KEY: missing.append("FINNHUB_API_KEY")
if missing:
    logger.error("Missing env vars: %s", ", ".join(missing))
    raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

CHAT_IDS = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]

# === State ===
sent_hashes            = deque(maxlen=1000)
sent_hashes_timestamps = {}
sent_hashes_lock       = threading.Lock()

option_cache            = {}
option_cache_timestamps = {}
option_cache_lock       = threading.Lock()

# === Sentiment Analyzer ===
analyzer = SentimentIntensityAnalyzer()

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        data = {
            "chat_id": chat_id,
            "text": message[:4096],
            "parse_mode": "Markdown",
        }
        resp = requests.post(url, data=data, timeout=10)
        if not resp.ok:
            info = resp.json()
            logger.error("Telegram API HTTPError for chat %s: %s", chat_id, info)
            resp.raise_for_status()
        logger.info("Alert sent to %s", chat_id)
        time.sleep(1)

# === Helpers ===
def match_ticker(text: str):
    txt = text.upper()
    return [t for t in LIQUID_TICKERS if re.search(rf"\b{re.escape(t)}\b", txt)]

def get_option_data(ticker: str):
    """Returns (atm_strike, option_price) or (None, None)"""
    now = datetime.utcnow()
    with option_cache_lock:
        if ticker in option_cache and (now - option_cache_timestamps[ticker]) < timedelta(minutes=15):
            return option_cache[ticker]

    try:
        # Current price
        q = requests.get(
            f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}",
            timeout=5,
        ).json()
        price = q.get("c") or 0
        if not price:
            raise ValueError("No current price")

        # Option chain
        oc = requests.get(
            f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}",
            timeout=5,
        ).json()

        best = (None, None, float("inf"))
        for contract in oc.get("data", []):
            for call in contract.get("options", {}).get("CALL", []):
                strike = call.get("strike", 0)
                diff = abs(strike - price)
                if diff < best[2]:
                    last = call.get("lastPrice") or call.get("ask") or 0
                    best = (strike, last, diff)

        result = (best[0], best[1]) if best[0] is not None else (None, None)
        with option_cache_lock:
            option_cache[ticker] = result
            option_cache_timestamps[ticker] = now
        return result

    except Exception as e:
        logger.warning("get_option_data(%s) failed: %s", ticker, e)
        return None, None

# === Core Logic ===
def fetch_and_analyze_news():
    logger.info("Starting scan...")
    now = datetime.utcnow()

    # 1) RSS from Yahoo Finance
    logger.info("Scanning RSS feed")
    feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")
    for entry in feed.entries:
        # skip >24h old
        ts = entry.get("published")
        if ts:
            try:
                dt = datetime.strptime(ts, "%a, %d %b %Y %H:%M:%S %z")
                if now - dt.replace(tzinfo=None) > timedelta(hours=24):
                    continue
            except:
                pass

        title   = entry.get("title", "")
        summary = entry.get("summary", "")
        content = f"{title} {summary}"

        _hash = hashlib.sha256(content.encode()).hexdigest()
        with sent_hashes_lock:
            cutoff = now - timedelta(hours=24)
            # prune old
            for h in list(sent_hashes_timestamps):
                if sent_hashes_timestamps[h] < cutoff:
                    sent_hashes_timestamps.pop(h, None)
                    try: sent_hashes.remove(h)
                    except: pass

            if _hash in sent_hashes:
                continue
            sent_hashes.append(_hash)
            sent_hashes_timestamps[_hash] = now

        score = analyzer.polarity_scores(content[:512])["compound"]
        if abs(score) < SENTIMENT_THRESHOLD:
            continue

        direction = "Bullish" if score > 0 else "Bearish"
        for t in match_ticker(content):
            strike, price = get_option_data(t)
            if strike is None or price is None:
                logger.info("Skipping %s: no option data", t)
                continue

            msg = (
                f"🚨 *Market News Alert*\n"
                f"🕒 {datetime.now():%Y-%m-%d %H:%M}\n"
                f"📰 {title}\n"
                f"🔄 {direction}\n\n"
                f"🎯 *Trade Setup*\n"
                f"• Ticker: {t}\n"
                f"• Side: {direction}\n"
                f"• Strike: {strike}\n"
                f"• Expiration: 2 weeks out\n"
                f"• Est Price: ${price:.2f}\n"
                f"• Sentiment score: {score:.2f}\n"
            )
            send_telegram_alert(msg)

    # 2) Finnhub news
    logger.info("Scanning Finnhub feed")
    for ticker in LIQUID_TICKERS:
        try:
            r = requests.get(
                f"https://finnhub.io/api/v1/news?symbol={ticker}&token={FINNHUB_API_KEY}",
                timeout=5,
            )
            r.raise_for_status()
            for item in r.json():
                dt = datetime.utcfromtimestamp(item.get("datetime", 0))
                if now - dt > timedelta(hours=24):
                    continue
                title   = item.get("headline", "")
                summary = item.get("summary", "")
                content = f"{title} {summary}"

                _hash = hashlib.sha256(content.encode()).hexdigest()
                with sent_hashes_lock:
                    if _hash in sent_hashes:
                        continue
                    sent_hashes.append(_hash)

                score = analyzer.polarity_scores(content[:512])["compound"]
                if abs(score) < SENTIMENT_THRESHOLD:
                    continue

                direction = "Bullish" if score > 0 else "Bearish"
                strike, price = get_option_data(ticker)
                if strike and price:
                    msg = (
                        f"🚨 *Market News Alert*\n"
                        f"🕒 {datetime.now():%Y-%m-%d %H:%M}\n"
                        f"📰 {title}\n"
                        f"🔄 {direction}\n\n"
                        f"🎯 *Trade Setup*\n"
                        f"• Ticker: {ticker}\n"
                        f"• Side: {direction}\n"
                        f"• Strike: {strike}\n"
                        f"• Expiration: 2 weeks out\n"
                        f"• Est Price: ${price:.2f}\n"
                        f"• Sentiment score: {score:.2f}\n"
                    )
                    send_telegram_alert(msg)

        except Exception as e:
            logger.debug("Finnhub fetch for %s failed: %s", ticker, e)

    app.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Scan complete.")

# === Scheduler ===
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MINUTES)
scheduler.start()
logger.info("Scheduler started, every %d minutes", SCAN_INTERVAL_MINUTES)

# === HTTP Endpoints ===
@app.route("/")
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route("/health")
def health():
    return jsonify(status="healthy", last_scan=app.last_scan)

@app.route("/test-alert")
def test_alert():
    try:
        send_telegram_alert("🚀 Test alert: bot is online.")
        return "Test alert sent.", 200
    except Exception as e:
        logger.error("Test-alert failed: %s", e)
        return f"Test-alert failed: {e}", 500

@app.route("/scan-now")
def scan_now():
    threading.Thread(target=fetch_and_analyze_news, daemon=True).start()
    return "On-demand scan triggered.", 200

if __name__ == "__main__":
    # just in case you ever run locally
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
