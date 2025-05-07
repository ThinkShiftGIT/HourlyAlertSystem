import os
import time
import requests
import threading
import feedparser
import hashlib
import logging
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, Tuple, Optional, List
from flask import Flask, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# === Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask Setup ===
app = Flask(__name__)

# === Environment Variables ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.5))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 5))
LIQUID_TICKERS = os.getenv("LIQUID_TICKERS", 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA').split(',')

# === Health/Status Tracking ===
state = {
    "last_scan_time": None,
    "next_scan_time": None,
    "last_alert_time": None,
    "alert_count": 0,
    "marketaux_calls": 0,
    "polygon_calls": 0,
    "yahoo_calls": 0
}

# === Globals ===
ticker_list = LIQUID_TICKERS.copy()
sent_hashes = deque(maxlen=1000)
daily_sentiment_scores: Dict[str, List[float]] = {ticker: [] for ticker in ticker_list}

def analyze_sentiment(text: str) -> float:
    positives = ["gain", "growth", "beat", "bullish"]
    negatives = ["loss", "fall", "bearish", "miss"]
    text = text.lower()
    score = sum(text.count(p) for p in positives) - sum(text.count(n) for n in negatives)
    return max(min(score / 3, 1.0), -1.0)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_alert(message):
    for chat_id in CHAT_IDS:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": chat_id.strip(), "text": message, "parse_mode": "Markdown"}
            )
            response.raise_for_status()
            logger.info(f"✅ Sent alert to {chat_id.strip()}")
            state["last_alert_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
            state["alert_count"] += 1
        except Exception as e:
            logger.error(f"❌ Failed to send alert to {chat_id.strip()}: {e}")

# === Option Data (Polygon + Fallback) ===
def get_option_data(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?apiKey={POLYGON_API_KEY}"
        response = requests.get(url)
        state["polygon_calls"] += 1
        if response.status_code != 200:
            raise Exception(response.text)
        data = response.json()
        chains = data.get("results", {}).get("options", [])
        if not chains:
            return None, None
        atm = sorted(chains, key=lambda x: abs(x["strike_price"] - x.get("underlying_price", 0)))[0]
        return atm["strike_price"], atm["last_price"]
    except Exception as e:
        logger.error(f"Polygon option data error: {e}")
        return None, None

def send_trade_alert(ticker: str, headline: str, sentiment: float, source: str):
    direction = "Bullish" if sentiment > 0 else "Bearish"
    strike, price = get_option_data(ticker)
    if not strike or not price:
        logger.warning(f"Missing option data for {ticker}. Skipping alert.")
        return
    message = f"""
🚨 *Market News Alert*
📰 {headline}
🔄 {direction}
📡 {source}

🎯 *Trade Setup*
• Ticker: {ticker}
• Strategy: Long {'Call' if sentiment > 0 else 'Put'}
• Strike: {strike}
• Price: ${price:.2f}
• Sentiment Score: {sentiment:.2f}
"""
    send_telegram_alert(message)

# === News Scan ===
def fetch_and_analyze_news():
    logger.info("Scanning news sources...")
    state["last_scan_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
    state["next_scan_time"] = (datetime.now() + timedelta(minutes=SCAN_INTERVAL_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        url = f"https://api.marketaux.com/v1/news/all?api_token={MARKETAUX_API_KEY}&filter_entities=true"
        r = requests.get(url)
        state["marketaux_calls"] += 1
        news = r.json().get("data", [])
        for item in news:
            content = f"{item.get('title', '')} {item.get('description', '')}"
            h = hashlib.sha256(content.encode()).hexdigest()
            if h in sent_hashes:
                continue
            sent_hashes.append(h)
            sentiment = analyze_sentiment(content)
            if abs(sentiment) >= SENTIMENT_THRESHOLD:
                for symbol in ticker_list:
                    if symbol in content:
                        send_trade_alert(symbol, item['title'], sentiment, "Marketaux")
                        daily_sentiment_scores[symbol].append(sentiment)
    except Exception as e:
        logger.error(f"News fetch error: {e}")

@app.route("/dashboard")
def dashboard():
    html = """
    <h2>📊 RealTimeTradeBot Dashboard</h2>
    <ul>
        <li><b>Last Scan:</b> {{ state.last_scan_time }}</li>
        <li><b>Next Scan:</b> {{ state.next_scan_time }}</li>
        <li><b>Last Alert:</b> {{ state.last_alert_time }}</li>
        <li><b>Alerts Today:</b> {{ state.alert_count }}</li>
        <li><b>Polygon Calls:</b> {{ state.polygon_calls }}</li>
        <li><b>Marketaux Calls:</b> {{ state.marketaux_calls }}</li>
        <li><b>Yahoo Calls:</b> {{ state.yahoo_calls }}</li>
    </ul>
    """
    return render_template_string(html, state=state)

@app.route("/dashboard/json")
def dashboard_json():
    return jsonify(state)

@app.route("/")
def home():
    return "✅ RealTimeTradeBot is running!"

@app.route("/test/mock_alert")
def trigger_mock_alert():
    send_trade_alert("AAPL", "Apple announces breakthrough in AI", 0.7, "Mock")
    return {"status": "Mock alert sent"}

def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    fetch_and_analyze_news()
    logger.info("Initial scan complete. App is live.")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    main()
