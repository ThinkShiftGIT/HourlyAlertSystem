# 📈 RealTimeTradeBot

**RealTimeTradeBot** is an automated real-time trading alert bot that scans news feeds and financial APIs for market-moving headlines. It uses sentiment analysis to detect bullish or bearish tones and sends actionable trade alerts via **Telegram**, optionally showing a **web dashboard** for status, logs, and diagnostics.

---

## 🚀 Features

- ✅ **Real-time News Scanning** (RSS + Finnhub API)
- 🧠 **Sentiment Analysis** using VADER
- 🔁 **Dynamic Ticker Refresh** — top 50 most liquid U.S. stocks updated daily
- 💬 **Telegram Alerts** (with fallback retries)
- 🌐 **Flask Dashboard** — trigger scans and monitor status
- 🗂️ **Scheduled Scans** with APScheduler
- 🧪 **/health and /test-alert** endpoints for diagnostics
- 📝 Optional **log file/email notifications** for monitoring

---

## 🧩 Requirements

```bash
pip install -r requirements.txt
```

---

## 📄 Environment Variables

Set the following variables in a `.env` file or your hosting environment:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_IDS=123456789,987654321
FINNHUB_API_KEY=your_finnhub_api_key
SENTIMENT_THRESHOLD=0.2
SCAN_INTERVAL_MINUTES=5
PORT=8080
```

- **TELEGRAM_BOT_TOKEN** – Get it from [@BotFather](https://t.me/BotFather)
- **TELEGRAM_CHAT_IDS** – One or more comma-separated Telegram user/group chat IDs
- **FINNHUB_API_KEY** – Get it from [Finnhub.io](https://finnhub.io)

---

## 🛠️ Running Locally

```bash
python main.py
```

Or, for production (via Waitress):

```bash
python3 -m waitress --port=$PORT main:app
```

---

## 🌐 Web Interface

Visit `http://localhost:8080/` for:

- Last scan timestamp
- One-click manual scan trigger
- Healthy status confirmation

---

## 📬 Example Alert

```
🚨 Market News Alert
🕒 2025-05-07 10:35
📰 Apple launches new AI chip for Macs
🔄 Bullish

🎯 Trade Setup
• Ticker: AAPL
• Side: Bullish
• Strike: 180
• Expiration: 2 weeks out
• Est Price: $2.50
• Sentiment score: 0.71
```

---

## 📦 Deployment Options

- 🟢 **Fly.io** / **Render.com** / **Railway.app**
- 💻 **VPS (e.g., DigitalOcean, EC2)** – Run `main.py` with `pm2` or `systemd`
- 🐳 Docker support (optional future enhancement)

---

## 🔧 Coming Soon / Optional Enhancements

- 📈 Add chart snapshots or option Greeks in alerts
- 🧠 LLM summarization of headlines
- 📧 Email fallback alerts (e.g., via SMTP)
- 📁 Logging to file with rotation

---

## 🧠 Author

Temitope Adekola | [Telegram: @TemiAlpha](https://t.me/TemiAlpha)

---

## 📜 License

MIT License – free to use, modify, and share.
