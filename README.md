# 📈 Real-Time Trade Alert Bot

A real-time Flask-based Telegram bot that scans financial news and options data, performs sentiment analysis, and sends actionable stock trade alerts. Built with free-tier APIs from **Polygon.io**, **Marketaux**, and **Yahoo Finance**, with intelligent fallback and full logging for reliability.

---

## 🚀 Features

- 🔄 **Live News Feed** from Marketaux + Yahoo RSS  
- 🧠 **Simple Sentiment Analysis** (custom rule-based)  
- 🧾 **Options Chain Analysis** using Polygon.io  
- 🛠️ **Fallback to Yahoo Finance** when Polygon fails  
- 📬 **Telegram Alerts** with trade setup details  
- 🗓️ **Scheduled Jobs** using APScheduler  
- 💡 **Custom Ticker List Management** via Telegram  
- 🐛 **Detailed Logs** for troubleshooting and deployment  

---

## 📦 Tech Stack

- **Backend**: Python + Flask  
- **Scheduler**: APScheduler  
- **Alert Delivery**: Telegram Bot API  
- **News API**: Marketaux, Yahoo RSS  
- **Option Data API**: Polygon.io  
- **Logging**: Python logging module  
- **Deployment**: [Render.com](https://render.com)  

---

## 🔧 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/realtimetradebot.git
cd realtimetradebot
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Environment Variables

Use Render's **Environment tab** or a local `.env` file (if testing locally):

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_IDS=123456789
MARKETAUX_API_KEY=your_marketaux_api_key
POLYGON_API_KEY=your_polygon_api_key
SENTIMENT_THRESHOLD=0.3
SCAN_INTERVAL_MINUTES=5
LIQUID_TICKERS=AAPL,TSLA,SPY,NVDA,MSFT,GOOG
```

> ⚠️ **Do not commit `.env` to GitHub**

---

## ▶️ Usage

### 🔁 Trigger a Mock Alert

Test if everything is working:

```bash
curl https://your-app.onrender.com/test/mock_alert
```

You should see a trade alert in your Telegram.

---

### 🧪 Telegram Bot Commands

Visit your bot and send:

- `/start` — wake the bot  
- `/list_tickers` — view tracked tickers  
- `/add_ticker_TSLA` — add a ticker  
- `/remove_ticker_TSLA` — remove a ticker  

---

## 📜 Example Alert

```
🚨 Market News Alert  
🕒 2025-05-07 08:45 (UTC-5)  
📰 Apple announces breakthrough in AI technology  
🔄 Bullish  
📡 Marketaux  

🎯 Trade Setup  
• Ticker: AAPL  
• Strategy: Long Call  
• Strike: 185  
• Expiration: 2 weeks  
• Est. Contract Price: $2.15  
• Reason: Sentiment score 0.60  
• Entry: ASAP  
• Exit: 50% profit or 3 days before expiration  
```

---

## 📂 Project Structure

```
├── main.py               # Main Flask app  
├── requirements.txt      # Python dependencies  
├── README.md             # Project documentation  
```

---

## 📈 APIs Used

- [Polygon.io](https://polygon.io/)  
- [Marketaux](https://www.marketaux.com/)  
- [Yahoo Finance RSS](https://finance.yahoo.com/news/rssindex)  
- [Telegram Bot API](https://core.telegram.org/bots/api)  

---

## 🛡️ License

MIT License — free for personal and commercial use.

---

## 🙌 Author

Built by **Temitope Adekola** | AI Engineer + Trading Strategist  
Need help with enhancements or hosting? Open an issue or reach out via [Telegram](https://t.me/your_username).
