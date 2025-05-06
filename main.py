#!/usr/bin/env python3
import os
import sys
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import logging
from logging.config import dictConfig
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
import feedparser
from dateutil import parser as dateparser
from bs4 import BeautifulSoup
from transformers import pipeline
from prometheus_client import Counter, Gauge, start_http_server
import sentry_sdk

# === Configuration ===
@dataclass
class Config:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv('TELEGRAM_BOT_TOKEN', ''))
    telegram_chat_ids: List[str] = field(default_factory=lambda: [cid.strip() for cid in os.getenv('TELEGRAM_CHAT_IDS', '1654552128').split(',') if cid.strip()])
    finnhub_api_key: str = field(default_factory=lambda: os.getenv('FINNHUB_API_KEY', ''))
    sentiment_threshold: float = field(default_factory=lambda: float(os.getenv('SENTIMENT_THRESHOLD', '0.5')))
    scan_interval_minutes: int = field(default_factory=lambda: int(os.getenv('SCAN_INTERVAL_MINUTES', '5')))
    liquid_tickers: List[str] = field(default_factory=lambda: [t.strip().upper() for t in os.getenv('LIQUID_TICKERS', 'AAPL,TSLA,SPY,MSFT,AMD,GOOG,META,NVDA,NFLX,AMZN,BA,JPM,BAC,INTC,DIS').split(',')])
    dry_run: bool = field(default_factory=lambda: os.getenv('DRY_RUN', 'false').lower() == 'true')
    port: int = field(default_factory=lambda: int(os.getenv('PORT', '8080')))
    metrics_port: int = field(default_factory=lambda: int(os.getenv('METRICS_PORT', '8000')))
    environment: str = field(default_factory=lambda: os.getenv('ENV', 'dev'))

config = Config()

# Validate critical config
if not config.telegram_bot_token or not config.finnhub_api_key:
    sys.stderr.write("ERROR: TELEGRAM_BOT_TOKEN and FINNHUB_API_KEY are required\n")
    sys.exit(1)

# === Structured JSON Logging ===
dictConfig({
    'version': 1,
    'formatters': {
        'json': {'()': 'pythonjsonlogger.jsonlogger.JsonFormatter', 'fmt': '%(asctime)s %(levelname)s %(name)s %(message)s'}
    },
    'handlers': {'default': {'class': 'logging.StreamHandler', 'formatter': 'json'}},
    'root': {'handlers': ['default'], 'level': 'INFO'}
})
logger = logging.getLogger(__name__)

# === Sentry Initialization ===
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=1.0)
    sentry_sdk.set_tag('env', config.environment)
    logger.info('Sentry initialized')

# === Flask App & Metrics ===
app = Flask(__name__)
alerts_sent = Counter('tradebot_alerts_sent', 'Number of Telegram alerts sent')
articles_processed = Counter('tradebot_articles_processed', 'Number of news articles processed')
errors = Counter('tradebot_errors', 'Number of errors encountered')
jobs_running = Gauge('tradebot_jobs_running', 'Number of scanner jobs currently running')

# === Graceful Shutdown ===
scheduler = None

def shutdown(signum, frame):
    logger.info('Received shutdown signal, stopping...')
    if scheduler:
        scheduler.shutdown(wait=False)
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# === Routes ===
@app.route('/')
def home():
    return '✅ RealTimeTradeBot is running!'

@app.route('/health')
def health():
    return jsonify(status='healthy', last_scan=getattr(app, 'last_scan_time', 'never'))

@app.route('/test-alert')
def test_alert():
    send_telegram_alert('*Test Alert!* This is a dry-run test.' if config.dry_run else '*Test Alert!*')
    return 'Test alert triggered.'

# === Utility Functions ===
def escape_markdown(text: str) -> str:
    return (text.replace('_', '\_')
                .replace('*', '\*')
                .replace('[', '\[')
                .replace('`', '\`'))

# === News Hash Deduplication ===
sent_hashes: Dict[str, float] = {}
hash_lock = threading.Lock()

def is_duplicate_and_mark(hash_val: str) -> bool:
    with hash_lock:
        # Prune older than 24h
        cutoff = time.time() - 86400
        for h, ts in list(sent_hashes.items()):
            if ts < cutoff:
                sent_hashes.pop(h, None)
        if hash_val in sent_hashes:
            return True
        sent_hashes[hash_val] = time.time()
        return False

# === Option Data Caching ===
option_cache: Dict[str, Tuple[float, Tuple[Optional[int], Optional[float]]]] = {}
CACHE_TTL = 60  # seconds

def get_option_data(ticker: str) -> Tuple[Optional[int], Optional[float]]:
    now = time.time()
    if ticker in option_cache:
        ts, data = option_cache[ticker]
        if now - ts < CACHE_TTL:
            return data
    try:
        quote = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={config.finnhub_api_key}").json()
        price = quote.get('c')
        if not price:
            raise ValueError('Invalid price')
        oc = requests.get(f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={config.finnhub_api_key}").json()
        atm, opt_price, diff = None, None, float('inf')
        for contract in oc.get('data', []):
            for call in contract.get('options', {}).get('CALL', []):
                d = abs(call['strike'] - price)
                if d < diff:
                    diff, atm = d, call['strike']
                    opt_price = call.get('lastPrice') or call.get('ask')
        result = (atm, opt_price)
        option_cache[ticker] = (now, result)
        return result
    except Exception as e:
        logger.error(f"Option data error for {ticker}: {e}")
        errors.inc()
        return None, None

# === Sentiment Analyzer ===
try:
    sentiment_analyzer = pipeline('sentiment-analysis', model='ProsusAI/finbert')
except Exception as e:
    logger.error(f"Sentiment load failed: {e}")
    sys.exit(1)

# === Telegram Alert ===
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def send_telegram_alert(message: str):
    msg = escape_markdown(message)
    for chat_id in config.telegram_chat_ids:
        if config.dry_run:
            logger.info(f"[DRY RUN] Would send to {chat_id}: {msg}")
            continue
        resp = requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
            data={'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}
        )
        try:
            resp.raise_for_status()
            alerts_sent.inc()
            time.sleep(1)
        except Exception as exc:
            logger.error(f"Telegram send error to {chat_id}: {exc} - {resp.text}")
            errors.inc()
            raise

# === Ticker Matching ===
def match_ticker(text: str) -> List[str]:
    txt = text.upper()
    return [t for t in config.liquid_tickers if f" {t} " in f" {txt} "]

# === Content Fetching ===
def get_full_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        paras = soup.find_all('p')
        return ' '.join(p.get_text(strip=True) for p in paras) or ''
    except Exception as e:
        logger.warning(f"Article fetch failed {url}: {e}")
        errors.inc()
        return ''

# === Core News Processing ===
def fetch_and_analyze_news():
    jobs_running.inc()
    try:
        for src in [
            {'type': 'rss', 'url': 'https://finance.yahoo.com/news/rssindex', 'name': 'Yahoo'},
            {'type': 'rss', 'url': 'https://feeds.reuters.com/reuters/businessNews', 'name': 'Reuters'},
            {'type': 'finnhub', 'url': 'https://finnhub.io/api/v1/news', 'name': 'Finnhub'}
        ]:
            logging.info(f"Scanning {src['name']}")
            articles = []
            if src['type'] == 'rss':
                feed = feedparser.parse(src['url'])
                for e in feed.entries:
                    dt = e.get('published') or e.get('updated')
                    try:
                        when = dateparser.parse(dt) if dt else None
                    except: when = None
                    if when and (time.time() - when.timestamp()) > 86400:
                        continue
                    content = f"{e.title} {e.get('summary','')} {get_full_article(e.link)}"
                    articles.append((e.title, content))
            else:
                for t in config.liquid_tickers:
                    resp = requests.get(f"{src['url']}?symbol={t}&token={config.finnhub_api_key}")
                    for item in resp.json():
                        when = dateparser.parse(item.get('datetime', ''), default=None)
                        if not when or (time.time() - when.timestamp()) > 86400:
                            continue
                        txt = f"{item['headline']} {item.get('summary','')}"
                        articles.append((item['headline'], txt))

            for title, content in articles:
                articles_processed.inc()
                h = hashlib.sha256(content.encode()).hexdigest()
                if is_duplicate_and_mark(h):
                    continue
                label = sentiment_analyzer(content[:512])[0]['label']
                score = 0.5 if label=='positive' else -0.5 if label=='negative' else 0
                if abs(score) < config.sentiment_threshold:
                    continue
                for ticker in match_ticker(content):
                    atm, optp = get_option_data(ticker)
                    if not atm or not optp:
                        continue
                    send_telegram_alert(
                        f"🚨 Market Alert\nHeadline: {title}\nTicker: {ticker}\nDirection: {'Bullish' if score>0 else 'Bearish'}\nStrike: {atm}\nPrice: ${optp:.2f}" )
        app.last_scan_time = time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Error in news fetch: {e}")
        errors.inc()
    finally:
        jobs_running.dec()

# === Main ===
def main():
    start_http_server(config.metrics_port)
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_analyze_news, 'interval', minutes=config.scan_interval_minutes)
    scheduler.start()
    app.run(host='0.0.0.0', port=config.port)

if __name__ == '__main__':
    main()
