# 🚀 RealTimeTradeBot

A real-time trading signal alert bot that monitors news headlines and sends option trade alerts via Telegram using sentiment analysis. Deployed on [Render](https://render.com/), this bot ensures consistent uptime and delivery with auto-restarts and web hosting.

## 🔍 Features

- Monitors Yahoo Finance RSS feed for high-impact headlines
- Analyzes sentiment using TextBlob
- Detects mentions of highly liquid US stocks
- Sends well-structured trade alerts via Telegram
- Uses hashed headlines to avoid duplicate alerts
- Runs continuously via Flask + Waitress on Render

## ⚙️ Technologies Used

- Python 3
- Flask (API keep-alive)
- TextBlob (sentiment analysis)
- Feedparser (RSS parsing)
- Telegram Bot API (alert delivery)
- Waitress (production WSGI server)
- Hosted on [Render](https://render.com/)

## 📦 Installation

Clone the repo:
```bash
git clone https://github.com/ThinkShiftGIT/RealTimeTradeBot.git
cd RealTimeTradeBot
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Create a `.env` or use secrets to store:

```
TELEGRAM_BOT_TOKEN=your_bot_token
FINNHUB_API_KEY=your_finnhub_key  # Optional, for option data later
```

## 🚀 Deploying to Render

1. Log in to [Render](https://render.com/)
2. Create a new Web Service
3. Connect your GitHub repo
4. Use the following settings:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python3 -m waitress --port=$PORT main:app`
   - Environment Variables: Add your bot token and API keys
5. Hit **Deploy**

Live endpoint: [https://realtimetradebot.onrender.com](https://realtimetradebot.onrender.com)

## 📡 Telegram Setup

- Create a Telegram bot with [@BotFather](https://t.me/BotFather)
- Get your bot token
- Start a chat with your bot
- Use this script to get your `chat_id`:

```python
import requests
TOKEN = "YOUR_BOT_TOKEN"
updates = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates").json()
print(updates)
```

## 📈 Sample Alert

```
🚨 Market News Alert
🕒 Date/Time: 2025-05-05 21:10 (UTC-5)
📰 Headline: Apple to Boost AI Spend in 2025
🔄 Impact: Bullish

🎯 Trade Setup
• Ticker: AAPL
• Strategy: Long Call
• Strike: ATM
• Expiration: 2 weeks out
• Est. Contract Price: ~$180
• Reason: Strong sentiment from real-time news
• POP: Likely >70% based on event-driven catalyst
• Entry: ASAP
• Exit Rule: 50% profit or 3 days before expiration

🔔 Action: Monitor trade; follow-up alert if exit rule is triggered.
```

---

## 💡 To-Do

- Integrate real-time option data via Tradier
- Add support for multiple Telegram chat IDs
- Expand news sources (e.g., Twitter, earnings feeds)

## 📜 License

MIT — free to use, modify, and share.

---

Built with ❤️ by [ThinkShiftGIT](https://github.com/ThinkShiftGIT)
