"""
股票技術分析 Dashboard - Streamlit 入口
專案：Project Gemini-Quant v4.0 (Portfolio Edition)
更新：新增歷史報告查看功能，移除所有 Emoji。
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dataprocess import compute_summary_metrics, get_stock_data, get_price_info
from utils import ArchiveManager
from utils.analyst import Analyst
from utils.portfolio import PortfolioManager

# 初始化常數
DATA_FILE = Path(__file__).parent / "data.json"
PERIOD_OPTIONS = {"1mo": "1 個月", "3mo": "3 個月", "6mo": "6 個月", "1y": "1 年", "5y": "5 年"}
MA_COLORS = {"MA10": "#ffeb3b", "MA20": "#ff9800", "MA50": "#9c27b0", "MA200": "#00e5ff"}

# 單例實例
_ARCHIVE = ArchiveManager()
_ANALYST = Analyst(_ARCHIVE)
_PM = PortfolioManager(DATA_FILE)
logger = logging.getLogger(__name__)

# 初始化 Session State
if "show_tx_form" not in st.session_state:
    st.session_state.show_tx_form = False
if "view_history" not in st.session_state:
    st.session_state.view_history = False

def build_candlestick_chart(df: pd.DataFrame, settings: dict) -> go.Figure:
    """繪製技術分析圖表。"""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="K 線", increasing_line_color="#26a69a", decreasing_line_color="#ef5350"
    ))
    for ma, color in MA_COLORS.items():
        if ma in df.columns and settings.get(f"show_{ma.lower()}"):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], mode="lines", name=ma, line=dict(color=color, width=1.5)))
    
    if settings.get("show_bb") and "BB_upper" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_upper"], mode="lines", name="布林上軌", line=dict(color="#03a9f4", width=1, dash="dash")))
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_lower"], mode="lines", name="布林下軌", line=dict(color="#03a9f4", width=1, dash="dash")))

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(30,30,30,0.9)",
        xaxis_rangeslider_visible=False, font=dict(color="#e0e0e0"), hovermode="x unified", height=450,
        margin=dict(t=10, b=10, l=10, r=10)
    )
    return fig

def render_pie_chart(holdings: list):
    """在側邊欄繪製持股比例餅圖。"""
    if not holdings:
        st.info("尚無資產配置數據")
        return
    labels = [h['ticker'] for h in holdings]
    values = [h['market_value'] for h in holdings]
    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.4, 
                                 marker=dict(colors=['#26a69a', '#ef5350', '#03a9f4', '#ffeb3b', '#9c27b0']))])
    fig.update_layout(
        showlegend=False, height=180, margin=dict(t=0, b=0, l=0, r=0),
        paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#e0e0e0")
    )
    st.plotly_chart(fig, use_container_width=True)

def run_ai_workflow(ticker: str, as_of_str: str, period: str, df: pd.DataFrame, settings: dict, current_price: float, total_market_value: float, api_key: str):
    """執行 AI 分析工作流。"""
    try:
        if not api_key:
            st.error("未偵測到 API Key，無法啟動 AI 分析。請在側邊欄輸入。")
            return

        distilled = _ARCHIVE.load_json(ticker, as_of_str, f"distilled_{period}")
        if not distilled:
            st.error("找不到蒸餾數據。")
            return

        portfolio_metrics = _PM.calculate_metrics(ticker, current_price)
        if portfolio_metrics and total_market_value > 0:
            market_val = portfolio_metrics.get("market_value", 0)
            portfolio_metrics["weight_pct"] = round((market_val / total_market_value) * 100, 2)
            portfolio_metrics["ticker"] = ticker

        as_of_dt = pd.to_datetime(as_of_str)
        hist_ctx = _ANALYST._get_historical_context(ticker, as_of_dt)
        save_dir = _ARCHIVE.get_path(ticker, as_of_str)
        image_path = save_dir / "chart_view.png"
        
        fig = build_candlestick_chart(df, settings)
        fig.write_image(str(image_path), width=1280, height=720, scale=1)

        with st.spinner("分析官處理中..."):
            report = _ANALYST.run_deep_analysis(ticker, distilled, hist_ctx, portfolio_data=portfolio_metrics, image_path=image_path, api_key=api_key)
            _ARCHIVE.save_text(ticker, as_of_str, "analysis_report.md", report)

        with st.spinner("執行官決策中..."):
            decision = _ANALYST.run_decision_summary(report, distilled, portfolio_data=portfolio_metrics, image_path=image_path, api_key=api_key)
            _ARCHIVE.save_json(ticker, as_of_str, "decision_summary.json", decision)

        st.success("AI 分析已更新。")
        st.rerun()
    except Exception as e:
        st.error(f"工作流錯誤: {e}")

def _filter_df_by_period(df: pd.DataFrame, period_key: str) -> pd.DataFrame:
    mapping = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 365 * 5}
    start = df.index.max() - timedelta(days=mapping.get(period_key, 365))
    return df.loc[df.index >= start]

def main() -> None:
    st.set_page_config(page_title="Gemini-Quant v4.0", layout="wide", initial_sidebar_state="expanded")
    
    chart_settings = {
        "show_ma10": True, "show_ma20": True, "show_ma50": True, "show_ma200": True, "show_bb": True
    }

    st.markdown("""
        <style>
        .stApp { background-color: #0e1117; }
        .stSidebar { background-color: #1a1a2e; }
        h1, h2, h3, p, label { color: #e0e0e0 !important; }
        [data-testid="stMetricValue"] { font-size: 1.5rem !important; font-weight: 600 !important; }
        [data-testid="stMetricLabel"] { font-size: 0.85rem !important; opacity: 0.8; }
        .stMetric { background-color: #1e1e2f; padding: 6px 12px !important; border-radius: 8px; border: 1px solid #303030; }
        hr { margin: 0.8rem 0 !important; }
        .css-1d391kg { padding-top: 1rem; }
        </style>
    """, unsafe_allow_html=True)

    watchlist = _PM.data.get("watchlist", [])
    portfolio_tickers = list(_PM.data.get("portfolio", {}).keys())
    
    with st.sidebar:
        st.title("控制面板")
        
        st.subheader("API 設定")
        user_api_key = st.text_input("Gemini API Key", type="password", help="請輸入您的 Google AI Studio API Key").strip()
        if not user_api_key:
            st.warning("分析功能已禁用 (缺少 Key)")
        else:
            st.success("API Key 已就緒")
        st.divider()

        period = st.selectbox("時間範圍", list(PERIOD_OPTIONS.keys()), format_func=lambda x: PERIOD_OPTIONS[x])
        selected_ticker = st.radio("觀察清單", watchlist)
        
        full_price_info_map = {}
        df_selected, name_selected, curr_p_selected = get_stock_data(selected_ticker, period, force_refresh=True)
        
        for pt in portfolio_tickers:
            if pt == selected_ticker:
                p_close = df_selected["Close"].iloc[-2] if df_selected is not None and len(df_selected) > 1 else curr_p_selected
                full_price_info_map[pt] = {"current": curr_p_selected, "prev_close": p_close}
            else:
                full_price_info_map[pt] = get_price_info(pt)
        
        global_summary = _PM.get_portfolio_summary(full_price_info_map)

        st.divider()
        new_ticker = st.text_input("新增觀察代碼", placeholder="例如 TSLA").strip().upper()
        if st.button("確認新增", use_container_width=True):
            if new_ticker and new_ticker not in watchlist:
                watchlist.append(new_ticker)
                _PM.data["watchlist"] = watchlist
                _PM.save()
                st.rerun()

        st.divider()
        st.subheader("技術指標")
        chart_settings["show_ma10"] = st.checkbox("MA10", True)
        chart_settings["show_ma20"] = st.checkbox("MA20", True)
        chart_settings["show_ma50"] = st.checkbox("MA50", True)
        chart_settings["show_ma200"] = st.checkbox("MA200", True)
        chart_settings["show_bb"] = st.checkbox("布林通道", True)

        st.divider()
        st.subheader("持股配置")
        render_pie_chart(global_summary["holdings"])

        st.divider()
        st.subheader("投資管理")
        if st.button("新增交易紀錄", use_container_width=True):
            st.session_state.show_tx_form = not st.session_state.show_tx_form
        
        if st.session_state.show_tx_form:
            with st.form("tx_form", clear_on_submit=True):
                t_ticker = st.text_input("代碼", value=selected_ticker).upper()
                t_type = st.selectbox("類型", ["buy", "sell"])
                t_date = st.date_input("交易日期", datetime.now())
                t_price = st.number_input("單價", min_value=0.01, format="%.2f")
                t_shares = st.number_input("股數", min_value=0.01, format="%.2f")
                if st.form_submit_button("提交交易紀錄"):
                    if t_ticker:
                        _PM.add_transaction(t_ticker, t_type, t_date.strftime("%Y-%m-%d"), t_price, t_shares)
                        st.session_state.show_tx_form = False
                        st.rerun()

    # --- 主面板 ---
    if df_selected is None:
        st.error(f"無法獲取數據: {selected_ticker}")
        return

    current_price = curr_p_selected
    total_market_val = global_summary.get('total_market_value', 0)
    total_inv_cost = global_summary.get('total_inventory_cost', 0)
    
    g1, g2, g3 = st.columns(3)
    g1.metric("資產總市值", f"${total_market_val:,.2f}")
    g2.metric("未實現損益", f"${global_summary['total_unrealized_pnl']:,.2f}", 
              delta=f"{global_summary['total_roi_pct']:.2f}% (ROI)")
    realized_pct = (global_summary['total_realized_pnl'] / total_inv_cost * 100) if total_inv_cost > 0 else 0
    g3.metric("已實現損益", f"${global_summary['total_realized_pnl']:,.2f}", delta=f"{realized_pct:+.2f}%")
    
    st.divider()

    st.subheader(f"標的分析: {selected_ticker}")
    df = _filter_df_by_period(df_selected, period)
    as_of_str = df.index.max().strftime("%Y-%m-%d")

    m1, m2, m3 = st.columns(3)
    m1.metric("公司名稱", name_selected)
    m2.metric("當前市價", f"${current_price:,.2f}")
    m3.metric("資料日期", as_of_str)

    st.plotly_chart(build_candlestick_chart(df, chart_settings), use_container_width=True)

    st.subheader(f"{selected_ticker} 持股診斷")
    active_holding = [h for h in global_summary["holdings"] if h["ticker"] == selected_ticker]
    if not active_holding:
        active_holding = [{
            "ticker": selected_ticker, "market_value": 0.0, "day_change_pct": 0.0, 
            "day_change_amt": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0, "roi_pct": 0.0
        }]
    h_df = pd.DataFrame(active_holding)
    display_df = h_df[["ticker", "market_value", "day_change_pct", "day_change_amt", "unrealized_pnl", "realized_pnl", "roi_pct"]]
    display_df.columns = ["代碼", "市值", "當日%", "當日盈虧", "未實現", "已實現", "ROI%"]
    st.dataframe(
        display_df.style.format({
            "市值": "{:,.2f}", "當日%": "{:+.2f}%", "當日盈虧": "{:+.2f}",
            "未實現": "{:+.2f}", "已實現": "{:+.2f}", "ROI%": "{:+.2f}%"
        }),
        use_container_width=True, hide_index=True
    )

    # 5. AI 分析
    st.divider()
    
    # 獲取該標的所有可用的歷史日期
    ticker_root = _ARCHIVE.root / selected_ticker.upper()
    available_dates = []
    if ticker_root.exists():
        available_dates = sorted([d.name for d in ticker_root.iterdir() if d.is_dir() and (d / "analysis_report.md").exists()], reverse=True)

    a_run, a_regen, a_history = st.columns(3)
    has_report = _ARCHIVE.exists(selected_ticker, as_of_str, "analysis_report.md")
    
    # 啟動/重新生成按鈕
    if a_regen.button("重新生成分析", use_container_width=True):
        if not user_api_key:
            st.error("未輸入 API Key 所以無法使用分析功能")
        else:
            run_ai_workflow(selected_ticker, as_of_str, period, df, chart_settings, current_price, total_market_val, user_api_key)
            
    elif a_run.button("啟動 AI 分析", use_container_width=True):
        if not has_report:
            if not user_api_key:
                st.error("未輸入 API Key 所以無法使用分析功能")
            else:
                run_ai_workflow(selected_ticker, as_of_str, period, df, chart_settings, current_price, total_market_val, user_api_key)
    
    # 歷史分析報告按鈕
    if a_history.button("歷史分析報告", use_container_width=True):
        st.session_state.view_history = not st.session_state.view_history

    # 歷史報告顯示邏輯
    target_report_date = as_of_str
    if st.session_state.view_history:
        if available_dates:
            target_report_date = st.selectbox("選擇歷史報告日期", available_dates)
        else:
            st.info("該標的尚無任何歷史報告紀錄")

    # 顯示報告內容
    if _ARCHIVE.exists(selected_ticker, target_report_date, "analysis_report.md"):
        report_md = _ARCHIVE.load_text(selected_ticker, target_report_date, "analysis_report.md")
        decision = _ARCHIVE.load_json(selected_ticker, target_report_date, "decision_summary.json")
        
        if decision:
            with st.container(border=True):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("趨勢", decision.get("trend", "N/A"))
                d2.metric("建議", decision.get("recommendation", "N/A"))
                d3.metric("進場/停損", f"{decision.get('entry_price')}/{decision.get('stop_loss')}")
                d4.metric("目標", decision.get("exit_price", "N/A"))
                
                rebalance_msg = decision.get("rebalance_reason", "N/A")
                if rebalance_msg != "N/A":
                    st.info(f"配置診斷： {rebalance_msg}")
                    
        st.markdown(f"### 深度分析報告 ({target_report_date})")
        st.markdown(report_md)
    elif st.session_state.view_history:
        st.warning(f"找不到 {target_report_date} 的分析結果")

if __name__ == "__main__":
    main()