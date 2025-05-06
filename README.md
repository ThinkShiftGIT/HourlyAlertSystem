# HourlyAlertSystem
# 📈 Real-Time Options Trade Alert Bot

A production-ready Python Telegram bot that scans real-time market news and earnings data to deliver **actionable options trade alerts** with high probability setups. Designed for retail traders navigating PDT constraints and limited capital accounts.

---

## 🔍 Features

- ✅ Real-time news sentiment analysis (via Yahoo Finance RSS + TextBlob)
- ✅ Earnings calendar scanning via Finnhub API
- ✅ Smart filtering of liquid tickers (AAPL, TSLA, SPY, MSFT, etc.)
- ✅ Structured trade alerts:
  - Option strategy (long call/put)
  - Strike & expiration recommendation
  - Trade rationale with sentiment context
- ✅ Telegram integration for direct mobile alerts
- ✅ Built for $200 accounts with PDT restrictions
- ✅ Auto-hosted using Flask & Replit with UptimeRobot

---

## 🚀 Live Demo

This bot runs continuously and sends live trade alerts to subscribed Telegram users.

> 🧪 Try it: [Live Replit Deployment](https://replit.com/@thinkshiftllc23/HourlyAlertSystem)

---

## 🔧 Setup Instructions

### 1. Clone or Fork This Repo

```bash
git clone https://github.com/ThinkShiftGIT/HourlyAlertSystem.git
