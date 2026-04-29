# RDA Stock Trading Bot

Algorithmic trading system for Indian equities (Nifty 500) using Dhan API.

## Project Configuration

| Setting | Value |
|---|---|
| **Broker** | Dhan (free API + paper trading) |
| **Capital (Phase 1)** | ₹1,00,000 |
| **Universe** | Nifty 500 |
| **Hosting (Phase 1)** | Home PC during market hours (09:15 – 15:30 IST) |
| **Hosting (Phase 2)** | Oracle Cloud Always Free VPS |
| **Risk per trade** | 2% of capital (₹2,000 max) |
| **Max open positions** | 5 |

## Architecture — Multi-Agent System

The system is split into 6 specialized agents, each with a single responsibility:

```
agents/
├── research/        News, earnings calendar, corporate actions, sector signals
├── fundamental/     Screener filters (P/E, ROE, debt, growth), F&O ban check
├── technical/       Indicators (RSI, MACD, EMA), patterns, entry/exit signals
├── risk/            Position sizing, stop-loss placement, exposure limits
├── execution/       Dhan API integration — order placement, status, fills
└── portfolio/       P&L tracking, daily MTM, STCG/LTCG bookkeeping
```

## Folder Structure

```
stock-trading/
├── agents/             Trading agents (see above)
├── strategies/         Strategy implementations (momentum, mean-reversion, etc.)
├── backtests/          Backtest scripts and results
├── data/               Historical data, instrument lists, cache
├── logs/               Trade logs, error logs, daily snapshots
├── config/             config.yaml — all tunable parameters
├── utils/              Logger, helpers, common utilities
├── tests/              Unit tests for each agent
├── notebooks/          Jupyter notebooks for analysis
├── main.py             Orchestrator — runs the daily cycle
├── requirements.txt    Python dependencies
├── .env.example        Template for API keys (copy to .env)
└── .gitignore          Excludes .env, logs, data, __pycache__
```

## Setup (Week 1)

1. **Install Python 3.10+**
2. **Create virtual environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate    # Windows
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure credentials**
   ```bash
   copy .env.example .env
   # Edit .env with your Dhan client_id and access_token
   ```
5. **Initialize git + GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial scaffold"
   git remote add origin https://github.com/<your-username>/rda-stock-trading.git
   git push -u origin main
   ```

## Roadmap

- **Week 1** — Scaffold + Dhan API connectivity test (paper account)
- **Week 2** — Technical agent + 1 strategy on paper trading
- **Week 3** — Risk + execution layer
- **Week 4** — Portfolio tracking + daily reports
- **Phase 2** — Migrate to Oracle Cloud, add fundamental + research agents
- **Phase 3** — Go live with ₹1L capital after 4+ weeks of profitable paper trading

## Key Principles

1. **Paper trade first** — never go live without 4 weeks of profitable paper results.
2. **Risk first, returns second** — every trade must have stop-loss before entry.
3. **Log everything** — every signal, order, fill, error. Critical for debugging.
4. **Version control everything** — commit working code, tag releases for production.

## Disclaimer

Algorithmic trading involves financial risk. Past backtest performance does not guarantee future returns. Use paper trading to validate strategies before deploying real capital.
