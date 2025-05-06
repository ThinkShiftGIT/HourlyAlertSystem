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
from typing import Optional

from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# === Logging ===
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

@app.route("/test-alert")
def test_alert():
    send_telegram_alert("🚀 Test alert: bot is online.")
    return "Test alert sent."

# === Configuration from ENV ===
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS          = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY")
TRADIER_API_KEY   = os.getenv("TRADIER_API_KEY")
TRADIER_ACCOUNT   = os.getenv("TRADIER_ACCOUNT")  # e.g. your 6YB56044
SENTIMENT_THRESH  = float(os.getenv("SENTIMENT_THRESHOLD", "0.5"))
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
LIQUID_TICKERS    = os.getenv("LIQUID_TICKERS", "").split(",")

for var_name in ("TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_IDS","FINNHUB_API_KEY","TRADIER_API_KEY","TRADIER_ACCOUNT"):
    if not globals()[var_name]:
        logger.error(f"Missing {var_name} env variable")
        raise RuntimeError(f"Missing {var_name}")

# === In-memory trackers ===
sent_hashes           = deque(maxlen=1000)
sent_hashes_timestamps= {}
sent_hashes_lock      = threading.Lock()

# === Sentiment Analyzer ===
sentiment_analyzer = SentimentIntensityAnalyzer()

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        payload = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
        r = requests.post(url, data=payload)
        r.raise_for_status()
        logger.info(f"Alert sent to {chat_id}")

# === Ticker Matcher ===
def match_ticker(text: str):
    txt = text.upper()
    return [t for t in LIQUID_TICKERS if re.search(rf"\b{re.escape(t)}\b", txt)]

# === Fetch Full Article ===
def get_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        return " ".join(p.get_text(strip=True) for p in soup.find_all("p"))
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return ""

# === Fetch Option Data from Finnhub ===
def get_option_data(ticker: str) -> tuple[Optional[float], Optional[float]]:
    # simple quote + find ATM call
    quote = requests.get(
        f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
    ).json()
    price = quote.get("c") or 0
    if price <= 0:
        return None, None

    chain = requests.get(
        f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_API_KEY}"
    ).json().get("data", [])
    atm, opt_price = None, None
    diff = float("inf")
    for ctr in chain:
        for call in ctr.get("options", {}).get("CALL", []):
            s = call["strike"]
            d = abs(s - price)
            if d < diff:
                diff, atm = d, s
                opt_price = call.get("lastPrice") or call.get("ask") or 0
    return (atm, opt_price) if atm else (None, None)

# === Place Tradier Option Order (stub) ===
def place_option_order(
    ticker: str,
    strike: float,
    expiration: str,
    quantity: int,
    side: str = "buy",
    option_type: str = "call"
) -> Optional[dict]:
    """Example Tradier trade link—adjust per your needs."""
    url = "https://api.tradier.com/v1/accounts/{}/orders".format(TRADIER_ACCOUNT)
    headers = {
        "Authorization": f"Bearer {TRADIER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "class": "option",
        "symbol": ticker,
        "quantity": quantity,
        "type": "market",
        "option_type": option_type,
        "side": side,
        "duration": "day",
        "price": None,
        "stop": None,
        "strike": strike,
        "expiry": expiration
    }
    resp = requests.post(url, json=body, headers=headers)
    if not resp.ok:
        logger.error(f"Tradier order failed {resp.json()}")
        return None
    return resp.json()

# === Core: Scan & Alert ===
def fetch_and_analyze_news():
    try:
        for src in (
            {"type":"rss","url":"https://finance.yahoo.com/news/rssindex"},
            {"type":"finnhub","url":"https://finnhub.io/api/v1/news"}
        ):
            logger.info(f"Scanning {src['type']}...")
            items = []
            if src["type"]=="rss":
                feed = feedparser.parse(src["url"])
                for e in feed.entries:
                    pub = e.get("published")
                    if pub and datetime.now() - datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z") > timedelta(hours=24):
                        continue
                    text = f"{e.title} {e.get('summary','')}"
                    items.append((e.title, text, e.link))
            else:
                for t in LIQUID_TICKERS:
                    r = requests.get(f"{src['url']}?symbol={t}&token={FINNHUB_API_KEY}")
                    for it in r.json():
                        dt = datetime.fromtimestamp(it.get("datetime",0))
                        if datetime.now() - dt > timedelta(hours=24):
                            continue
                        title = it.get("headline","")
                        text  = f"{title} {it.get('summary','')}"
                        items.append((title, text, None))

            for title, content, link in items:
                h = hashlib.sha256(content.encode()).hexdigest()
                with sent_hashes_lock:
                    cutoff = datetime.now() - timedelta(hours=24)
                    # expire old
                    for k,v in list(sent_hashes_timestamps.items()):
                        if v < cutoff:
                            sent_hashes.remove(k)
                            del sent_hashes_timestamps[k]
                    if h in sent_hashes:
                        continue
                    sent_hashes.append(h)
                    sent_hashes_timestamps[h] = datetime.now()

                vs = sentiment_analyzer.polarity_scores(content)
                score = vs["compound"]
                if abs(score) < SENTIMENT_THRESH:
                    continue

                tks = match_ticker(content)
                for tk in tks:
                    atm, optp = get_option_data(tk)
                    if atm and optp:
                        msg = (
                            f"🚨 *{title}*\n"
                            f"👀 Ticker: {tk}   Sentiment: {score:.2f}\n"
                            f"💡 ATM Strike: {atm}   ≈${optp:.2f}\n"
                            f"🔗 {link or ''}"
                        )
                        send_trade_alert(msg)
                        # optional: place_option_order(tk, atm, (datetime.now()+timedelta(days=14)).strftime("%Y-%m-%d"), 1)
        app.last_scan_time = datetime.utcnow().isoformat()
    except Exception as e:
        logger.error(f"Scan error: {e}")

@app.route("/scan-now")
def scan_now():
    threading.Thread(target=fetch_and_analyze_news, daemon=True).start()
    return "Scanning initiated.", 202

# === Boot ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, "interval", minutes=SCAN_INTERVAL_MIN)
    scheduler.start()

    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

if __name__=="__main__":
    main()
