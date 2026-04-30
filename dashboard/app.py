"""RDA Stock Trading — Main Dashboard.

Run from project root:
    streamlit run dashboard/app.py

Opens at http://localhost:8501
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from streamlit_autorefresh import st_autorefresh

from dashboard.styles import CUSTOM_CSS
from dashboard.data_loader import (
    load_positions, load_trades, load_signals, load_equity_curve,
    load_events, load_universe, load_candidates, get_kpis,
    load_fundamentals, load_news, load_backtest_results, load_backtest_trades,
)
from utils.config_loader import load_config

# ---------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------
st.set_page_config(
    page_title="RDA Stock Trading",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

load_dotenv()
config = load_config()

# ---------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------
with st.sidebar:
    st.markdown("### Settings")

    auto_refresh = st.toggle("Auto-refresh (10s)", value=False)
    if auto_refresh:
        st_autorefresh(interval=10_000, key="autorefresh")

    if st.button("Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### Mode")
    mode = os.getenv("TRADING_MODE", config["account"]["trading_mode"])
    badge_class = "badge-mode-live" if mode == "LIVE" else "badge-mode-paper"
    st.markdown(f'<span class="badge {badge_class}">{mode}</span>',
                unsafe_allow_html=True)

    st.divider()
    st.markdown("### Account")
    st.text(f"Client ID: {os.getenv('DHAN_CLIENT_ID', 'N/A')}")
    st.text(f"Capital: Rs.{config['account']['capital']:,}")

    st.divider()
    st.markdown("### Quick Actions")
    if st.button("Run paper demo", use_container_width=True):
        st.info("Run from terminal: `python scripts\\run_paper_demo.py`")

    st.divider()
    st.markdown("### About")
    st.caption("RDA Stock Trading Bot")
    st.caption("Phase 1 — Paper Trading")
    st.caption(f"Data refreshed: {datetime.now().strftime('%H:%M:%S')}")

# ---------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------
st.markdown(f"""
<div class="app-header">
  <div>
    <h1>📈 RDA Stock Trading — Algo Bot</h1>
    <p>Multi-agent trading system · Nifty 500 universe · {mode} mode</p>
  </div>
  <div style="text-align: right;">
    <p style="font-size: 11px; opacity: 0.7;">LAST UPDATE</p>
    <p style="font-size: 16px; font-weight: 600;">{datetime.now().strftime('%H:%M:%S · %d %b %Y')}</p>
  </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------
positions = load_positions()
trades = load_trades()
signals = load_signals()
equity = load_equity_curve()
events = load_events()
universe = load_universe()
candidates = load_candidates()
fundamentals = load_fundamentals()
news = load_news()
bt_results = load_backtest_results()
bt_trades = load_backtest_trades()
kpis = get_kpis(config, positions, trades)


# ---------------------------------------------------------------
# KPI CARDS
# ---------------------------------------------------------------
def kpi_card(label: str, value: str, delta: str = "", delta_class: str = "neutral") -> str:
    delta_html = f'<div class="kpi-delta {delta_class}">{delta}</div>' if delta else ""
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {delta_html}
    </div>
    """


col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.markdown(kpi_card(
        "Capital",
        f"Rs.{kpis['capital']:,.0f}",
        f"Cash: Rs.{kpis['cash']:,.0f}",
        "neutral",
    ), unsafe_allow_html=True)

with col2:
    pnl = kpis["unrealized_pnl"]
    cls = "positive" if pnl >= 0 else "negative"
    arrow = "▲" if pnl >= 0 else "▼"
    st.markdown(kpi_card(
        "Unrealized P&L",
        f"Rs.{pnl:+,.0f}",
        f"{arrow} {abs(pnl)/kpis['capital']*100:.2f}% of capital" if kpis['capital'] else "",
        cls,
    ), unsafe_allow_html=True)

with col3:
    today = kpis["realized_today"]
    cls = "positive" if today >= 0 else "negative"
    arrow = "▲" if today >= 0 else "▼"
    st.markdown(kpi_card(
        "Realized Today",
        f"Rs.{today:+,.0f}",
        f"{arrow} {abs(today)/kpis['capital']*100:.2f}% today" if kpis['capital'] else "",
        cls,
    ), unsafe_allow_html=True)

with col4:
    st.markdown(kpi_card(
        "Open Positions",
        str(kpis["open_positions"]),
        f"Invested: Rs.{kpis['invested']:,.0f}",
        "neutral",
    ), unsafe_allow_html=True)

with col5:
    wr = kpis["win_rate"]
    cls = "positive" if wr >= 50 else ("negative" if wr < 40 else "neutral")
    st.markdown(kpi_card(
        "Win Rate",
        f"{wr:.0f}%",
        f"{kpis['total_trades']} total trades",
        cls,
    ), unsafe_allow_html=True)

st.markdown("<br/>", unsafe_allow_html=True)

# ---------------------------------------------------------------
# TABS
# ---------------------------------------------------------------
(tab1, tab_watch, tab2, tab3, tab_fund, tab_news,
 tab_bt, tab4, tab5, tab6) = st.tabs([
    "📊 Overview",
    "🔥 Watchlist",
    "💼 Positions & Trades",
    "🎯 Signals",
    "💰 Fundamentals",
    "📰 News",
    "🧪 Backtest",
    "🤖 Agent Activity",
    "📈 Performance",
    "⚙️ Universe",
])

# ===============================================================
# TAB 1 — OVERVIEW
# ===============================================================
with tab1:
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown("### Equity Curve")
        if equity.empty:
            st.info("No equity history yet. Run the bot to start tracking performance.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=equity["date"],
                y=equity["capital"] + equity.get("unrealized_pnl", 0),
                mode="lines+markers",
                line=dict(color="#a78bfa", width=2),
                fill="tozeroy",
                fillcolor="rgba(167, 139, 250, 0.1)",
                name="Equity",
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                height=320,
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.markdown("### Today's Activity")
        today_str = datetime.now().date().isoformat()
        today_signals = signals[signals["timestamp"].astype(str).str.startswith(today_str)] \
            if not signals.empty else signals
        n_total = len(today_signals)
        n_executed = (today_signals["status"] == "executed").sum() if n_total else 0
        n_rejected = (today_signals["status"] == "rejected").sum() if n_total else 0

        st.metric("Stocks scanned", len(universe) if not universe.empty else "—")
        st.metric("Signals generated", n_total)
        st.metric("Trades executed", int(n_executed))
        st.metric("Rejected by risk", int(n_rejected))

    # Top 5 candidates snapshot at bottom of Overview
    if not candidates.empty:
        st.markdown("### 🔥 Top 5 — Watchlist Preview")
        st.caption("Full list in 🔥 Watchlist tab")
        top5 = candidates.head(5).copy()
        cols = st.columns(5)
        for i, (_, row) in enumerate(top5.iterrows()):
            with cols[i]:
                grade_color = {"A+": "#00d97e", "A": "#00d97e",
                               "B": "#ffa726", "C": "#ff8a65"}.get(row["grade"], "#8b92a0")
                st.markdown(f"""
                <div class="kpi-card" style="border-left: 4px solid {grade_color};">
                    <div class="kpi-label">{row['symbol']}</div>
                    <div style="font-size: 16px; font-weight: 700; color: {grade_color};">
                        {row['grade']} · {row['score']:.0f}
                    </div>
                    <div style="font-size: 11px; color: var(--text-secondary); margin-top: 6px;">
                        Rs.{row['last_close']:.0f} · RSI {row['rsi']:.0f}
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted);">
                        {row['distance_from_high_pct']:.1f}% from high
                    </div>
                </div>
                """, unsafe_allow_html=True)

# ===============================================================
# TAB — WATCHLIST (ranked candidates)
# ===============================================================
with tab_watch:
    if candidates.empty:
        st.warning(
            "No candidates yet. Run a bulk scan first:\n\n"
            "`python scripts\\scan_universe.py --limit 100`\n\n"
            "(Use --limit 500 for full Nifty 500.)"
        )
    else:
        # Top stats
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stocks Ranked", len(candidates))
        c2.metric("Top Score", f"{candidates['score'].max():.1f}")
        c3.metric("A/A+ Grades", int(candidates["grade"].isin(["A", "A+"]).sum()))
        c4.metric("Median RSI", f"{candidates['rsi'].median():.0f}")

        st.markdown("<br/>", unsafe_allow_html=True)

        col_left, col_right = st.columns([3, 2])

        with col_left:
            st.markdown("### 🏆 Top 25 — Closest to Breakout")
            top25 = candidates.head(25).copy()

            # Color grade column
            def grade_emoji(g):
                return {"A+": "🟢 A+", "A": "🟢 A", "B": "🟡 B",
                        "C": "🟠 C", "D": "🔴 D", "F": "⚪ F"}.get(g, g)
            top25["grade"] = top25["grade"].apply(grade_emoji)

            st.dataframe(
                top25[["rank", "symbol", "sector", "grade", "score",
                       "last_close", "rsi", "distance_from_high_pct",
                       "volume_ratio", "ema_aligned", "trend_pct"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "rank": st.column_config.NumberColumn("#", width="small"),
                    "score": st.column_config.NumberColumn("Score", format="%.1f"),
                    "last_close": st.column_config.NumberColumn("LTP", format="Rs.%.2f"),
                    "rsi": st.column_config.NumberColumn("RSI", format="%.1f"),
                    "distance_from_high_pct": st.column_config.NumberColumn(
                        "% from high", format="%.2f%%"),
                    "volume_ratio": st.column_config.NumberColumn(
                        "Vol ratio", format="%.2fx"),
                    "trend_pct": st.column_config.NumberColumn(
                        "5d %", format="%+.2f%%"),
                    "ema_aligned": "EMA align",
                },
            )

        with col_right:
            st.markdown("### 📊 Score Distribution")
            fig = px.histogram(
                candidates, x="score", nbins=30,
                color_discrete_sequence=["#a78bfa"],
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                height=240,
                xaxis_title="Score", yaxis_title="Stocks",
            )
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("### 🏭 Top Sectors by Avg Score")
            if "sector" in candidates.columns:
                sec_score = candidates.groupby("sector")["score"].agg(["mean", "count"]) \
                    .sort_values("mean", ascending=False).head(10)
                sec_score = sec_score[sec_score["count"] >= 3]    # min 3 stocks
                fig = px.bar(
                    x=sec_score["mean"], y=sec_score.index,
                    orientation="h",
                    color=sec_score["mean"],
                    color_continuous_scale=["#5e72e4", "#a78bfa"],
                )
                fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=300,
                    xaxis_title="Avg Score", yaxis_title="",
                    yaxis=dict(autorange="reversed"),
                    showlegend=False, coloraxis_showscale=False,
                )
                st.plotly_chart(fig, use_container_width=True)

        st.markdown("### 🌡️ RSI Heatmap by Sector")
        if "sector" in candidates.columns:
            heat = candidates.pivot_table(
                index="sector", values=["rsi", "score"], aggfunc="mean"
            ).round(1).sort_values("score", ascending=False)
            heat["count"] = candidates.groupby("sector").size()
            heat = heat[heat["count"] >= 3]   # ignore sectors with <3 stocks
            heat.columns = ["Avg RSI", "Avg Score", "Stocks"]
            st.dataframe(heat, use_container_width=True)


# ===============================================================
# TAB 2 — POSITIONS & TRADES
# ===============================================================
with tab2:
    st.markdown("### Open Positions")
    if positions.empty:
        st.info("No open positions. The bot hasn't taken any trades yet (or all were closed).")
    else:
        st.dataframe(
            positions,
            use_container_width=True,
            hide_index=True,
            column_config={
                "entry_price": st.column_config.NumberColumn("Entry", format="Rs.%.2f"),
                "current_price": st.column_config.NumberColumn("LTP", format="Rs.%.2f"),
                "stop_loss": st.column_config.NumberColumn("SL", format="Rs.%.2f"),
                "target": st.column_config.NumberColumn("Target", format="Rs.%.2f"),
                "unrealized_pnl": st.column_config.NumberColumn("P&L", format="Rs.%+.0f"),
            },
        )

    st.markdown("### Closed Trades")
    if trades.empty:
        st.info("No closed trades yet.")
    else:
        st.dataframe(
            trades.tail(50).iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

# ===============================================================
# TAB 3 — SIGNALS
# ===============================================================
with tab3:
    st.markdown("### Recent Signals")
    if signals.empty:
        st.info("No signals generated yet. Run the strategy during market hours.")
    else:
        # Add status badge column
        def status_badge(s):
            if s == "executed": return "✅ Executed"
            if s == "rejected": return "⚠️ Rejected"
            if s == "filtered": return "🚫 Filtered"
            return s
        display = signals.copy()
        if "status" in display.columns:
            display["status"] = display["status"].apply(status_badge)
        st.dataframe(display, use_container_width=True, hide_index=True)

# ===============================================================
# TAB — FUNDAMENTALS
# ===============================================================
with tab_fund:
    if fundamentals.empty:
        st.warning(
            "No fundamentals data yet. Refresh weekly:\n\n"
            "`python -m agents.fundamental.screener 100`\n\n"
            "(Number = how many stocks to fetch. ~1 stock per 0.4s.)"
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stocks covered", len(fundamentals))
        good_pe = (fundamentals["pe_ratio"].between(5, 30)).sum()
        good_roe = (fundamentals["roe"] >= 15).sum()
        low_debt = (fundamentals["debt_to_equity"] <= 0.5).sum()
        c2.metric("Quality PE (5-30)", int(good_pe))
        c3.metric("ROE >= 15%", int(good_roe))
        c4.metric("Low debt (<= 0.5)", int(low_debt))

        st.markdown("### Filters")
        f1, f2, f3 = st.columns(3)
        with f1:
            min_mcap = st.slider("Min market cap (Rs.cr)", 0, 50000, 1000, 500)
        with f2:
            max_pe_filter = st.slider("Max P/E", 0, 100, 60, 5)
        with f3:
            min_roe_filter = st.slider("Min ROE %", 0, 50, 12, 2)

        view = fundamentals[
            (fundamentals["market_cap_cr"] >= min_mcap)
            & (fundamentals["pe_ratio"] <= max_pe_filter)
            & (fundamentals["pe_ratio"] > 0)
            & (fundamentals["roe"] >= min_roe_filter)
        ].sort_values("market_cap_cr", ascending=False)

        st.markdown(f"### Filtered: {len(view)} stocks pass quality bar")
        st.dataframe(
            view[["symbol", "company", "sector", "market_cap_cr", "pe_ratio",
                  "pb_ratio", "roe", "debt_to_equity", "earnings_growth",
                  "revenue_growth", "dividend_yield"]],
            use_container_width=True, hide_index=True,
            column_config={
                "market_cap_cr": st.column_config.NumberColumn("M-Cap (Cr)", format="%.0f"),
                "pe_ratio": st.column_config.NumberColumn("P/E", format="%.1f"),
                "pb_ratio": st.column_config.NumberColumn("P/B", format="%.1f"),
                "roe": st.column_config.NumberColumn("ROE%", format="%.1f"),
                "debt_to_equity": st.column_config.NumberColumn("D/E", format="%.2f"),
                "earnings_growth": st.column_config.NumberColumn("EPS Gr%", format="%+.1f"),
                "revenue_growth": st.column_config.NumberColumn("Rev Gr%", format="%+.1f"),
                "dividend_yield": st.column_config.NumberColumn("Div%", format="%.2f"),
            },
        )


# ===============================================================
# TAB — NEWS
# ===============================================================
with tab_news:
    if news.empty:
        st.warning(
            "No news yet. Refresh during pre-market:\n\n"
            "`python -m agents.research.news_scraper`"
        )
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Stocks with news", news["symbol"].nunique())
        c2.metric("Negative", int((news["sentiment"] == "negative").sum()))
        c3.metric("Positive", int((news["sentiment"] == "positive").sum()))

        st.markdown("### Filter")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            sentiment_filter = st.selectbox(
                "Sentiment", ["All", "negative", "positive", "neutral"])
        with col_f2:
            symbol_filter = st.text_input("Symbol search", "")

        view = news.copy()
        if sentiment_filter != "All":
            view = view[view["sentiment"] == sentiment_filter]
        if symbol_filter:
            view = view[view["symbol"].str.contains(symbol_filter.upper(), na=False)]

        view = view.sort_values("fetched_at", ascending=False).head(100)

        # Custom render for sentiment
        for _, row in view.iterrows():
            sent = row.get("sentiment", "neutral")
            color = {"negative": "#ff5b5b", "positive": "#00d97e",
                     "neutral": "#5e72e4"}.get(sent, "#5e72e4")
            url = row.get("url", "")
            link_html = f'<a href="{url}" target="_blank" style="color: var(--accent); text-decoration: none;">read &rarr;</a>' if url else ""
            st.markdown(f"""
            <div class="activity-item" style="border-left-color: {color};">
              <div class="activity-meta">{row.get('published_at', '')} - {row.get('publisher', '')}</div>
              <div>
                <span class="activity-symbol">{row['symbol']}</span>
                <span class="activity-text">{row['headline']}</span>
                <span style="margin-left: 8px;">{link_html}</span>
              </div>
            </div>
            """, unsafe_allow_html=True)


# ===============================================================
# TAB — BACKTEST
# ===============================================================
with tab_bt:
    if bt_results.empty:
        st.warning(
            "No backtest results yet. Run from terminal:\n\n"
            "`python backtests\\run_backtest.py --limit 100 --years 2`\n\n"
            "(Takes ~2-3 minutes for 100 stocks.)"
        )
    else:
        # Aggregated by strategy
        agg = bt_results.groupby("strategy").agg(
            symbols=("symbol", "count"),
            total_trades=("trades", "sum"),
            total_wins=("wins", "sum"),
            avg_return=("total_return_pct", "mean"),
            avg_dd=("max_drawdown_pct", "mean"),
            avg_pf=("profit_factor", "mean"),
            avg_sharpe=("sharpe", "mean"),
        ).round(2)
        agg["win_rate"] = (agg["total_wins"] / agg["total_trades"] * 100).round(1)

        st.markdown("### Strategy Summary")
        st.dataframe(agg, use_container_width=True)

        st.markdown("### Per-Symbol Breakdown")
        strat_pick = st.selectbox("Filter strategy",
                                   ["All"] + bt_results["strategy"].unique().tolist())
        view = bt_results
        if strat_pick != "All":
            view = view[view["strategy"] == strat_pick]
        view = view.sort_values("total_return_pct", ascending=False)
        st.dataframe(view, use_container_width=True, hide_index=True)

        if not bt_trades.empty:
            st.markdown("### P&L Distribution (all backtested trades)")
            fig = px.histogram(
                bt_trades, x="pnl_pct", color="strategy",
                nbins=50, barmode="overlay",
                color_discrete_sequence=["#a78bfa", "#5e72e4"],
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                height=320,
            )
            st.plotly_chart(fig, use_container_width=True)


# ===============================================================
# TAB 4 — AGENT ACTIVITY (Live Feed)
# ===============================================================
with tab4:
    st.markdown("### How Agents Communicate")

    flow_col1, flow_col2 = st.columns([1, 1])

    with flow_col1:
        st.markdown("""
        <div class="section-card">
          <div class="section-title">📐 Pipeline Flow</div>
          <div style="line-height: 2;">
            <div>1️⃣ <b>Orchestrator</b> triggers scan</div>
            <div>2️⃣ <b>Research Agent</b> → checks F&O ban, news</div>
            <div>3️⃣ <b>Fundamental Agent</b> → P/E, ROE filter</div>
            <div>4️⃣ <b>Technical Agent</b> → RSI, MACD, signal</div>
            <div>5️⃣ <b>Risk Agent</b> → sizing + veto power</div>
            <div>6️⃣ <b>Execution Agent</b> → places order via Dhan</div>
            <div>7️⃣ <b>Portfolio Agent</b> → records position</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    with flow_col2:
        # Agent activity counts
        st.markdown("""
        <div class="section-card">
          <div class="section-title">📊 Agent Stats (today)</div>
        </div>
        """, unsafe_allow_html=True)

        if events:
            ev_df = pd.DataFrame(events)
            today_str = datetime.now().date().isoformat()
            today_ev = ev_df[ev_df["timestamp"].astype(str).str.startswith(today_str)]
            agent_counts = today_ev["agent"].value_counts() if len(today_ev) else pd.Series(dtype=int)
            for agent in ["orchestrator", "research", "fundamental", "technical",
                          "risk", "execution", "portfolio"]:
                count = int(agent_counts.get(agent, 0))
                st.text(f"  {agent:14s} {count} events")
        else:
            st.info("No events yet.")

    st.markdown("### Live Activity Feed")
    if not events:
        st.info("No agent activity yet. Run the bot to see agents talking in real-time.")
    else:
        feed_html = '<div class="activity-feed">'
        for e in events:
            level = e.get("level", "info")
            ts = e.get("timestamp", "")[:19].replace("T", " ")
            agent = e.get("agent", "unknown")
            symbol = e.get("symbol", "")
            action = e.get("action", "")
            details = e.get("details", "")
            sym_html = f'<span class="activity-symbol">{symbol}</span> ' if symbol else ""
            feed_html += f"""
            <div class="activity-item {level}">
              <div class="activity-meta">{ts}</div>
              <div>
                <span class="activity-agent">{agent}</span>
                {sym_html}
                <span class="activity-text">{action} — {details}</span>
              </div>
            </div>
            """
        feed_html += "</div>"
        st.markdown(feed_html, unsafe_allow_html=True)

# ===============================================================
# TAB 5 — PERFORMANCE
# ===============================================================
with tab5:
    if trades.empty:
        st.info("No closed trades yet to analyze.")
    else:
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### P&L Distribution")
            if "pnl_net" in trades.columns:
                fig = px.histogram(
                    trades, x="pnl_net", nbins=30,
                    color_discrete_sequence=["#a78bfa"],
                )
                fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("### Trades by Strategy")
            if "strategy" in trades.columns:
                strat_counts = trades["strategy"].value_counts()
                fig = px.pie(
                    values=strat_counts.values,
                    names=strat_counts.index,
                    hole=0.5,
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Performance Metrics")
        m1, m2, m3, m4 = st.columns(4)
        wins = trades[trades["pnl_net"].astype(float) > 0]
        losses = trades[trades["pnl_net"].astype(float) <= 0]
        avg_win = wins["pnl_net"].astype(float).mean() if len(wins) else 0
        avg_loss = losses["pnl_net"].astype(float).mean() if len(losses) else 0
        profit_factor = abs(wins["pnl_net"].astype(float).sum() /
                            losses["pnl_net"].astype(float).sum()) \
            if len(losses) and losses["pnl_net"].astype(float).sum() != 0 else 0
        m1.metric("Total trades", len(trades))
        m2.metric("Avg win", f"Rs.{avg_win:+,.0f}")
        m3.metric("Avg loss", f"Rs.{avg_loss:+,.0f}")
        m4.metric("Profit factor", f"{profit_factor:.2f}")

# ===============================================================
# TAB 6 — UNIVERSE
# ===============================================================
with tab6:
    st.markdown("### Nifty 500 Universe")
    if universe.empty:
        st.warning("Universe not loaded. Run: `python scripts\\build_instrument_master.py`")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total stocks", len(universe))
        c2.metric("Sectors", universe["sector"].nunique() if "sector" in universe.columns else "—")
        c3.metric("Mapped to Dhan", universe["dhan_security_id"].notna().sum() if "dhan_security_id" in universe.columns else "—")

        if "sector" in universe.columns:
            st.markdown("### Sector Distribution")
            sec = universe["sector"].value_counts().head(15)
            fig = px.bar(
                x=sec.values, y=sec.index, orientation="h",
                color=sec.values,
                color_continuous_scale=["#5e72e4", "#a78bfa"],
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                height=400,
                yaxis=dict(autorange="reversed"),
                showlegend=False,
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Stock List")
        search = st.text_input("🔍 Search by symbol or company name", "")
        view = universe.copy()
        if search:
            mask = view["symbol"].str.contains(search.upper(), na=False)
            if "company_name" in view.columns:
                mask |= view["company_name"].str.contains(search, case=False, na=False)
            view = view[mask]
        st.dataframe(view, use_container_width=True, hide_index=True, height=400)

# ---------------------------------------------------------------
# FOOTER
# ---------------------------------------------------------------
st.markdown("<br/>", unsafe_allow_html=True)
st.caption(
    f"RDA Stock Trading · Phase 1 · Last refresh {datetime.now().strftime('%H:%M:%S')} · "
    f"Auto-refresh: {'ON' if auto_refresh else 'OFF'}"
)
