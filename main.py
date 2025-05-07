import os 
import time
import json
import logging
import requests
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from collections import deque
from typing import Optional, Tuple
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# === Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Flask App ===
app = Flask(__name__)

# === Environment Variables ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "1654552128").split(",")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY")
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 15))
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", 0.6))

# === Tickers to Monitor ===
TICKERS = [
    "NVDA", "TSLA", "AAPL", "AMZN", "PLTR", "AMD", "SMCI", "HIMS", "F", "LCID",
    "UPST", "RIVN", "MSFT", "BAC", "SOFI", "NU", "HOOD", "MARA", "PLUG", "QBTS"
]

# === Alert Cache ===
sent_hashes = deque(maxlen=100)
analyzer = SentimentIntensityAnalyzer()

# === Telegram Alerts ===
def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        logger.warning("Telegram not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        for chat_id in TELEGRAM_CHAT_IDS:
            data = {"chat_id": chat_id.strip(), "text": message[:4096], "parse_mode": "Markdown"}
            r = requests.post(url, data=data)
            r.raise_for_status()
        logger.info("✅ Sent alert to Telegram.")
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")

# === Polygon Price ===
def get_price_polygon(ticker: str) -> Optional[float]:
    try:
        url = f"https://api.polygon.io/v2/last/nbbo/{ticker}?apiKey={POLYGON_API_KEY}"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        return float(data.get("results", {}).get("bid", 0))
    except Exception as e:
        logger.error(f"Polygon price error: {e}")
        return None

# === Polygon Options ===
def get_option_data_polygon(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?apiKey={POLYGON_API_KEY}"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json().get("results", {}).get("options", [])
        if not data:
            return None, None
        option = data[0]
        return option.get("details", {}).get("strike_price"), option.get("last_quote", {}).get("ask")
    except Exception as e:
        logger.error(f"Polygon option error: {e}")
        return None, None

# === Marketaux News ===
def fetch_marketaux_news() -> list:
    try:
        url = f"https://api.marketaux.com/v1/news/all?api_token={MARKETAUX_API_KEY}&language=en&filter_entities=true"
        r = requests.get(url)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        logger.error(f"Marketaux error: {e}")
        return []

# === Main Scanner ===
def scan_and_alert():
    logger.info("🔍 Starting news scan...")
    articles = fetch_marketaux_news()
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')}"
        if not content:
            continue
        h = hash(content)
        if h in sent_hashes:
            continue
        sent_hashes.append(h)

        for ticker in TICKERS:
            if ticker in content:
                sentiment = analyzer.polarity_scores(content)
                compound = sentiment['compound']
                if abs(compound) < SENTIMENT_THRESHOLD:
                    continue

                strike, option_price = get_option_data_polygon(ticker)
                last_price = get_price_polygon(ticker)

                if strike is None or option_price is None or last_price is None:
                    logger.warning(f"Skipping {ticker} due to missing data.")
                    continue

                alert = {
                    "ticker": ticker,
                    "headline": article.get('title'),
                    "sentiment": round(compound, 3),
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }

                try:
                    with open("alerts.json", "r") as f:
                        existing = json.load(f)
                except:
                    existing = []

                existing.append(alert)
                with open("alerts.json", "w") as f:
                    json.dump(existing[-100:], f, indent=2)

                msg = f"""
🚨 *Trade Alert: {ticker}*
📰 {article.get('title')}
📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

*Market Price:* ${last_price:.2f}
*Option Strike:* ${strike:.2f}
*Ask Price:* ${option_price:.2f}
*Sentiment Score:* {compound:+.2f}
*Source:* Marketaux
                """
                send_telegram_alert(msg)
                break

# === Flask Routes ===
@app.route("/")
def home():
    return "✅ RealTimeTradeBot is running."

@app.route("/health")
def health():
    return {"status": "healthy", "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')}

@app.route("/test/mock_alert")
def test_alert():
    send_telegram_alert("🧪 This is a test alert from RealTimeTradeBot.")
    return {"result": "Sent"}

@app.route("/alerts")
def get_alerts():
    try:
        with open("alerts.json", "r") as f:
            alerts = json.load(f)
        return jsonify(alerts)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/trigger_scan")
def trigger_scan():
    scan_and_alert()
    return jsonify({"result": "Scan triggered manually."})

@app.route("/dashboard")
def dashboard():
    try:
        with open("alerts.json", "r") as f:
            alerts = json.load(f)
    except:
        alerts = []
    return render_template("dashboard.html", alerts=alerts, tickers=TICKERS, time=time)

# === Launch App ===
def main():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_and_alert, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("📆 Scheduler started.")
    scan_and_alert()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    main()
