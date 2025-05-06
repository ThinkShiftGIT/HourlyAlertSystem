# RealTimeTradeBot 📈

A real-time trading alert bot built with Python. It scrapes Yahoo Finance news, analyzes sentiment, detects high-probability opportunities, and sends alerts via Telegram.

---

## 🚀 Live Demo

Deployed and running at:
➡️ [https://realtimetradebot.onrender.com](https://realtimetradebot.onrender.com)

---

## 💡 Features

- ✅ Real-time RSS news scanning (Yahoo Finance)
- ✅ Sentiment analysis using TextBlob
- ✅ Matches tickers against a list of highly liquid US stocks
- ✅ Sends actionable alerts to Telegram
- ✅ Includes trade direction, strike, expiration, and POP
- ✅ Logs and tracks alerts to avoid duplicates
- ✅ Hosted on Render with Flask + Waitress

---

## 🛠️ Tech Stack

- Python 3.11
- Flask + Waitress (deployment)
- Feedparser (news scraping)
- TextBlob (sentiment analysis)
- Telegram Bot API (alerting)
- Hosted on Render.com

---

## 🧪 Setup & Run Locally

```bash
git clone https://github.com/ThinkShiftGIT/RealTimeTradeBot.git
cd RealTimeTradeBot

# Install dependencies
pip install -r requirements.txt

# Add secrets via .env or Render dashboard
# TELEGRAM_BOT_TOKEN=your_bot_token
# FINNHUB_API_KEY=your_finnhub_key (future integration)

# Run the bot
python main.py
```

---

## 📦 Deploy on Render

1. Fork or clone this repo
2. Go to [https://dashboard.render.com](https://dashboard.render.com)
3. Create a new **Web Service**
4. Connect to your GitHub and pick this repo
5. Set Build Command: `pip install -r requirements.txt`
6. Set Start Command: `python3 -m waitress --port=$PORT main:app`
7. Add your **Secrets** under Environment tab

---

## 🧠 Roadmap

- ✅ Real-time news scanning & alerting
- 🔜 Tradier options chain integration
- 🔜 Twitter finance sentiment feed
- 🔜 Earnings calendar integration
- 🔜 SQLite alert log for backtest & history

---

## 👤 Author

[ThinkShiftGIT](https://github.com/ThinkShiftGIT)

Built with 💻 and 📊 in 2025
