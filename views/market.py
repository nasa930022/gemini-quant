"""
views/market.py - 市場分析看板頁面
包含：render_market_dashboard() 及所有相關 helper 函式
Phase 2 重構：從 main.py 分離
Phase 5 UX：新增集中度警示 Banner 與今日報告狀態標示
"""

import tempfile
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta
from pathlib import Path


# --- Helper 函式 ---

def _filter_df_by_period(df: pd.DataFrame, period_key: str) -> pd.DataFrame:
    mapping = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 365 * 5}
    if df is None or df.empty:
        return df
    df.index = pd.to_datetime(df.index)
    start_date = df.index.max() - timedelta(days=mapping.get(period_key, 365))
    return df.loc[df.index >= start_date]


def style_pnl(val):
    if isinstance(val, (int, float)):
        color = '#26a69a' if val > 0 else '#ef5350' if val < 0 else '#e0e0e0'
        return f'color: {color}'
    return ''


def format_portfolio_df(df: pd.DataFrame):
    RENAME_MAP = {
        "ticker": "股票代碼", "total_shares": "持股數", "avg_cost": "平均成本",
        "market_value": "當前市值", "inventory_cost": "庫存成本", "cumulative_buy_cost": "累計投入",
        "unrealized_pnl": "未實現損益", "realized_pnl": "已實現損益", "roi_pct": "報酬率",
        "day_change_amt": "當日變動", "day_change_pct": "當日%"
    }
    df = df.rename(columns=RENAME_MAP)
    format_dict = {
        "持股數": "{:.2f}", "平均成本": "{:.2f}", "當前市值": "{:,.2f}",
        "庫存成本": "{:,.2f}", "累計投入": "{:,.2f}", "未實現損益": "{:+,.2f}",
        "已實現損益": "{:+,.2f}", "報酬率": "{:+.2f}%", "當日變動": "{:+,.2f}", "當日%": "{:+.2f}%"
    }
    actual_formats = {k: v for k, v in format_dict.items() if k in df.columns}
    styled_df = df.style.format(actual_formats)
    color_cols = ["未實現損益", "已實現損益", "報酬率", "當日變動", "當日%"]
    available_color_cols = [c for c in color_cols if c in df.columns]
    return styled_df.map(style_pnl, subset=available_color_cols)


# 廣泛型 ETF 清單，用於豁免集中度警示
_DIVERSIFIED_ETFS = {"VOO", "VT", "VXUS", "SPY", "IVV", "VTI", "QQQ"}


@st.cache_data(ttl=3600, show_spinner="正在載入股票數據...")
def _cached_get_stock_data(ticker: str, username: str, force_refresh: bool = False):
    """包裹 get_stock_data 加入 1 小時快取，避免重複呼叫 yfinance。"""
    from dataprocess import get_stock_data
    return get_stock_data(ticker, username, force_refresh=force_refresh)

@st.cache_data(ttl=300, show_spinner="極速刷新最新投資組合股價...")
def _cached_get_latest_prices(tickers: tuple):
    """使用輕量化快取極速取得所有追蹤清單股價。"""
    from dataprocess import get_latest_prices
    return get_latest_prices(list(tickers))


# --- 主渲染函式 ---

def render_market_dashboard(archive, portfolio_mgr, analyst, period, show_ma, show_bb):
    """
    渲染市場分析看板。
    接受外部初始化的 archive / portfolio_mgr / analyst 實例以避免重複初始化。
    """
    username = st.session_state.username
    st.header("📈 市場分析看板")

    # --- 全域資產統計 ---
    summary = {}
    if st.session_state.auth_status:
        tracked = portfolio_mgr.get_tracked_tickers(username)
        price_map = {}
        if tracked:
            price_map = _cached_get_latest_prices(tuple(tracked))
        summary = portfolio_mgr.get_portfolio_summary(username, price_map)
        g1, g2, g3 = st.columns(3)
        g1.metric("資產總市值", f"${summary.get('total_market_value', 0):,.2f}")
        g2.metric("未實現損益", f"${summary.get('total_unrealized_pnl', 0):,.2f}",
                  delta=f"{summary.get('total_roi_pct', 0):.2f}%")
        g3.metric("已實現損益", f"${summary.get('total_realized_pnl', 0):,.2f}")

        # --- Phase 5 UX：集中度風險 Banner ---
        total_mv = summary.get('total_market_value', 0)
        if total_mv > 0:
            for h in summary.get("holdings", []):
                t_sym = h.get("ticker", "")
                mv = h.get("market_value", 0)
                weight = (mv / total_mv) * 100
                if weight > 20 and t_sym not in _DIVERSIFIED_ETFS:
                    st.warning(
                        f"⚠️ 持倉集中度警示：**{t_sym}** 佔整體資產 **{weight:.1f}%**，超過建議上限 20%。"
                        f" 建議分批減碼以降低單一標的風險。"
                    )

        st.divider()

    # --- 股票查詢 ---
    ticker = st.text_input("輸入股票代碼", value=st.session_state.current_ticker).upper()
    st.session_state.current_ticker = ticker
    df_raw, distilled = _cached_get_stock_data(ticker, username)

    if df_raw is not None:
        df = _filter_df_by_period(df_raw, period)
        current_p = float(df['Close'].iloc[-1])
        as_of_str = df.index.max().strftime("%Y-%m-%d")

        st.subheader(f"標的分析: {ticker}")
        m1, m2, m3 = st.columns(3)
        m1.metric("代碼", ticker)
        m2.metric("當前市價", f"${current_p:,.2f}")
        m3.metric("資料日期", as_of_str)

        # --- K 線圖 ---
        fig = go.Figure(data=[go.Candlestick(
            x=df.index, open=df['Open'], high=df['High'],
            low=df['Low'], close=df['Close'], name="K線"
        )])
        colors = {"MA10": "#ffeb3b", "MA20": "#ff9800", "MA50": "#9c27b0", "MA200": "#00e5ff"}
        for ma in show_ma:
            if ma in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=colors.get(ma), width=1.2)))
        if show_bb and "BB_upper" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_upper"], name="布林上軌",
                                     line=dict(color="rgba(173, 216, 230, 0.4)", dash="dash")))
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_lower"], name="布林下軌",
                                     line=dict(color="rgba(173, 216, 230, 0.4)", dash="dash"), fill='tonexty'))
        fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

        # --- 持股診斷表格 ---
        st.subheader(f"{ticker} 持股診斷")
        prev_close_p = float(df['Close'].iloc[-2]) if len(df) > 1 else current_p
        
        # 直接計算單一標的指標，避免對全域投資組合進行無效的高耗能運算
        holding_metrics = portfolio_mgr.calculate_metrics(username, ticker, current_p, prev_close=prev_close_p)
        active_holding = [holding_metrics] if holding_metrics else []
        st.dataframe(format_portfolio_df(pd.DataFrame(active_holding)), use_container_width=True, hide_index=True)

        # --- AI 分析區塊 ---
        if st.session_state.auth_status:
            st.divider()
            strategy = archive.load_strategy(username)
            # API Key 優先用使用者設定，若沒有則 fallback 到環境變數 (已由 load_dotenv 注入)
            import os
            api_key = strategy.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")

            report_base = archive.get_user_dir(username, "reports") / ticker
            available_dates = []
            if report_base.exists():
                available_dates = sorted(
                    [d.name for d in report_base.iterdir()
                     if d.is_dir() and (d / "analysis_report.md").exists()],
                    reverse=True
                )

            a_run, a_regen, a_history = st.columns(3)
            has_report = (report_base / as_of_str / "analysis_report.md").exists()

            # --- Phase 5 UX：今日報告狀態標示 ---
            if has_report:
                a_run.button("✅ 今日報告已生成", use_container_width=True, disabled=True, type="secondary")
            else:
                if a_run.button("🚀 啟動 AI 分析", use_container_width=True):
                    if not api_key:
                        st.error("未提供 API Key，請在「個人策略設定」輸入，或於 .env 設定 GEMINI_API_KEY。")
                    else:
                        with st.spinner("AI 視覺與數據分析中..."):
                            p_metrics = portfolio_mgr.calculate_metrics(username, ticker, current_p, prev_close_p)
                            if summary.get('total_market_value', 0) > 0 and p_metrics.get('market_value', 0) > 0:
                                p_metrics['weight_pct'] = round(
                                    (p_metrics.get('market_value', 0) / summary['total_market_value']) * 100, 2)
                            else:
                                p_metrics['weight_pct'] = 0.0
                            temp_dir = Path(tempfile.gettempdir())
                            img_path = temp_dir / f"{ticker}_chart.png"
                            fig.write_image(str(img_path))
                            report = analyst.run_deep_analysis(
                                username, ticker, distilled, p_metrics, image_path=img_path, api_key=api_key)
                            archive.save_text(username, "reports", "analysis_report.md", report,
                                              ticker=ticker, date=as_of_str)
                            decision = analyst.run_decision_summary(
                                username, report, portfolio_data=p_metrics, api_key=api_key)
                            archive.save_json(username, "reports", "decision_summary", decision,
                                              ticker=ticker, date=as_of_str)
                            st.rerun()

            if a_regen.button("🔄 重新生成分析", use_container_width=True):
                if not api_key:
                    st.error("未提供 API Key。")
                else:
                    with st.spinner("重新生成視覺與數據分析中..."):
                        p_metrics = portfolio_mgr.calculate_metrics(username, ticker, current_p, prev_close_p)
                        if summary.get('total_market_value', 0) > 0 and p_metrics.get('market_value', 0) > 0:
                            p_metrics['weight_pct'] = round(
                                (p_metrics.get('market_value', 0) / summary['total_market_value']) * 100, 2)
                        else:
                            p_metrics['weight_pct'] = 0.0
                        temp_dir = Path(tempfile.gettempdir())
                        img_path = temp_dir / f"{ticker}_chart.png"
                        fig.write_image(str(img_path))
                        report = analyst.run_deep_analysis(
                            username, ticker, distilled, p_metrics, image_path=img_path, api_key=api_key)
                        archive.save_text(username, "reports", "analysis_report.md", report,
                                          ticker=ticker, date=as_of_str)
                        decision = analyst.run_decision_summary(
                            username, report, portfolio_data=p_metrics, api_key=api_key)
                        archive.save_json(username, "reports", "decision_summary", decision,
                                          ticker=ticker, date=as_of_str)
                        st.rerun()

            if a_history.button("📂 歷史分析報告", use_container_width=True):
                st.session_state.view_history = not st.session_state.view_history

            # --- 顯示報告 ---
            target_date = as_of_str
            if st.session_state.view_history:
                if available_dates:
                    target_date = st.selectbox("選擇歷史報告日期", available_dates)
                else:
                    st.info("尚無歷史報告")

            report_md = archive.load_text(username, "reports", "analysis_report.md",
                                          ticker=ticker, date=target_date)
            decision = archive.load_json(username, "reports", "decision_summary",
                                         ticker=ticker, date=target_date)

            if report_md:
                if decision:
                    with st.container(border=True):
                        st.write(f"### 🎯 投資決策摘要 ({target_date})")
                        d1, d2, d3, d4 = st.columns(4)
                        d1.metric("趨勢", decision.get("trend", "N/A"))
                        d2.metric("建議", decision.get("recommendation", "N/A"))
                        entry = decision.get("entry_price", "N/A")
                        sl = decision.get("stop_loss", "N/A")
                        d3.metric("進場 / 停損", f"{entry} / {sl}")
                        d4.metric("目標", decision.get("exit_price", "N/A"))
                        note = decision.get("personalized_note", "")
                        if note:
                            st.info(f"💡 配置建議：{note}")

                st.markdown(f"### 📄 深度分析報告 ({target_date})")
                st.markdown(report_md)
