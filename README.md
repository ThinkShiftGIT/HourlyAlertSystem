# 📈 Real-Time Stock News Sentiment Alert System

This Flask-based application monitors real-time stock market news, extracts relevant sentiment, and alerts the user via Telegram when strong bullish or bearish signals are detected for tracked tickers. It also includes a dashboard to monitor alerts and system health.

---

## 🚀 Features

- 🔄 **Real-Time News Feed** (via [Marketaux API](https://www.marketaux.com/))
- 💬 **Sentiment Analysis** (via VADER)
- 📈 **Live Quote and Option Chain Lookup** (via [Polygon.io API](https://polygon.io/))
- 📲 **Telegram Alert Delivery**
- 🧠 **Duplicate Detection** via content hashing
- 🌐 **Web Dashboard** for live monitoring
- 🧾 **alerts.json Logging** (last 100 alerts)
- ⏰ **Scheduler** with configurable scan frequency

---

## 📊 Tracked Tickers

This deployment currently monitors the following 20 tickers:

```
NVDA, TSLA, AAPL, AMZN, PLTR, AMD, SMCI, HIMS, F, LCID,  
UPST, RIVN, MSFT, BAC, SOFI, NU, HOOD, MARA, PLUG, QBTS
```

---

## ⚙️ Environment Variables

| Variable                | Required | Description                                |
|-------------------------|----------|--------------------------------------------|
| `TELEGRAM_BOT_TOKEN`    | ✅       | Telegram bot API token                     |
| `TELEGRAM_CHAT_IDS`     | ✅       | Comma-separated list of Telegram chat IDs  |
| `POLYGON_API_KEY`       | ✅       | API key for polygon.io                     |
| `MARKETAUX_API_KEY`     | ✅       | API key for Marketaux                      |
| `SCAN_INTERVAL_MINUTES` | Optional | How often to scan for news (default: 15)   |
| `SENTIMENT_THRESHOLD`   | Optional | e.g., `0.6` for strong signal filtering     |
| `TICKERS`               | Optional | Comma-separated list of tickers to scan    |

---

## 📂 File Structure

```
.
├── main.py                 # Main application file
├── alerts.json            # Rolling log of recent alerts
├── requirements.txt       # Python dependencies
├── templates/
│   └── dashboard.html     # Web dashboard HTML
```

---

## 🖥️ Web Dashboard

Visit `/dashboard` on your deployed app to:
- View **tracked tickers**
- See **recent alerts**
- Monitor **system health status**

Example:
```
https://your-app.onrender.com/dashboard
```

---

## 📡 Telegram Alert Example

```
🚨 Trade Alert: TSLA
📰 Tesla reports record deliveries for Q2
📅 2025-05-07 11:34:12 UTC

*Market Price:* $187.45  
*Option Strike:* $190.00  
*Ask Price:* $4.10  
*Source:* Marketaux
```

---

## 🔒 Security Note

This app uses free APIs. You should:
- Keep your GitHub repo **private**
- Never expose your `.env` or secrets in public commits
- Rotate API keys periodically

---

## ✅ To-Do / Future Enhancements

- [ ] UI to manage tickers from the dashboard
- [ ] Add email or SMS fallback
- [ ] Store alerts in database or Google Sheets
- [ ] Support sentiment-based trading strategy suggestions

---

## 🧠 Credits

Built by [Temitope Adekola](https://github.com/yourusername)  
Powered by: Flask, Telegram API, Marketaux, Polygon.io, VADER Sentiment

---

## 📝 License

MIT License
