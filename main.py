"""
main.py - Project Gemini-Quant v4.0 (Personalized Platform Edition)
全量整合：身份驗證、市場看盤、策略配置、資產管理。
修正：優化表格顯示位數與漲跌顏色。
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
from datetime import datetime, timedelta
from pathlib import Path

# 載入自定義模組
from utils.archive import ArchiveManager
from utils.portfolio import PortfolioManager
from utils.analyst import Analyst
from dataprocess import get_stock_data

# --- 1. 基礎設定與實例初始化 ---
st.set_page_config(page_title="Gemini-Quant v4.0", layout="wide", initial_sidebar_state="expanded")

_ARCHIVE = ArchiveManager()
_PM = PortfolioManager(_ARCHIVE)
_ANALYST = Analyst(_ARCHIVE)
AUTH_FILE = Path("storage/auth.json")

# 自定義 CSS (提升專業感)
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; }
    .stMetric { background-color: #1e1e2f; padding: 10px; border-radius: 10px; border: 1px solid #303030; }
    h1, h2, h3, p, label { color: #e0e0e0 !important; }
    .stDataFrame { background-color: #1e1e2f; border-radius: 10px; }
    .stButton>button { width: 100%; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# --- 2. 輔助函數：表格美化邏輯 ---

def _filter_df_by_period(df: pd.DataFrame, period_key: str) -> pd.DataFrame:
    """根據選擇的時間範圍裁切數據"""
    mapping = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 365 * 5}
    if df is None or df.empty: return df
    df.index = pd.to_datetime(df.index)
    start_date = df.index.max() - timedelta(days=mapping.get(period_key, 365))
    return df.loc[df.index >= start_date]

def style_pnl(val):
    """根據數值正負回傳 CSS 顏色"""
    if isinstance(val, (int, float)):
        color = '#26a69a' if val > 0 else '#ef5350' if val < 0 else '#e0e0e0'
        return f'color: {color}'
    return ''

def format_portfolio_df(df: pd.DataFrame):
    """格式化 DataFrame：中文化欄位、控制位數與顏色"""
    
    # 1. 定義中文化映射表
    RENAME_MAP = {
        "ticker": "股票代碼",
        "total_shares": "持股數",
        "avg_cost": "平均成本",
        "market_value": "當前市值",
        "inventory_cost": "庫存成本",
        "cumulative_buy_cost": "累計投入",
        "unrealized_pnl": "未實現損益",
        "realized_pnl": "已實現損益",
        "roi_pct": "報酬率",
        "day_change_amt": "當日變動",
        "day_change_pct": "當日%"
    }
    
    # 先執行改名
    df = df.rename(columns=RENAME_MAP)
    
    # 2. 定義對應中文欄位的格式 (這裡必須使用中文名稱作為 Key)
    format_dict = {
        "持股數": "{:.2f}",
        "平均成本": "{:.2f}",
        "當前市值": "{:,.2f}",
        "庫存成本": "{:,.2f}",
        "累計投入": "{:,.2f}",
        "未實現損益": "{:+,.2f}",
        "已實現損益": "{:+,.2f}",
        "報酬率": "{:+.2f}%",
        "當日變動": "{:+,.2f}",
        "當日%": "{:+.2f}%"
    }
    
    # 確保欄位存在才進行格式化
    actual_formats = {k: v for k, v in format_dict.items() if k in df.columns}
    
    # 套用格式
    styled_df = df.style.format(actual_formats)
    
    # 3. 定義需要變色的中文欄位
    color_cols = ["未實現損益", "已實現損益", "報酬率", "當日變動", "當日%"]
    available_color_cols = [c for c in color_cols if c in df.columns]
    
    # 套用顏色邏輯 (正數綠色，負數紅色)
    return styled_df.applymap(style_pnl, subset=available_color_cols)

def handle_auth(mode, user, pw):
    """處理使用者帳號安全機制"""
    if not AUTH_FILE.exists():
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text("{}", encoding="utf-8")
    auth_data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    user = user.strip().lower()

    if mode == "註冊":
        if not user or not pw: return False, "欄位不可為空"
        if user in auth_data: return False, "此帳號已存在"
        auth_data[user] = pw
        AUTH_FILE.write_text(json.dumps(auth_data), encoding="utf-8")
        _ARCHIVE.get_user_dir(user) 
        return True, "註冊成功，請登入"
    elif mode == "登入":
        if auth_data.get(user) == pw: return True, "登入成功"
        return False, "帳號或密碼錯誤"
    return False, "錯誤"

# --- 3. Session State 狀態管理 ---
if "auth_status" not in st.session_state:
    st.session_state.auth_status = False
    st.session_state.username = "guest"
if "current_ticker" not in st.session_state:
    st.session_state.current_ticker = "NVDA"

# --- 4. 側邊欄：導覽與設定 ---
with st.sidebar:
    st.title("🛡️ Gemini-Quant")
    
    if not st.session_state.auth_status:
        st.subheader("解鎖個人化功能")
        auth_tab = st.tabs(["登入", "註冊"])
        with auth_tab[0]:
            l_u = st.text_input("帳號", key="l_u").lower()
            l_p = st.text_input("密碼", type="password", key="l_p")
            if st.button("登入系統"):
                success, msg = handle_auth("登入", l_u, l_p)
                if success:
                    st.session_state.auth_status = True
                    st.session_state.username = l_u
                    st.rerun()
                else: st.error(msg)
        with auth_tab[1]:
            r_u = st.text_input("帳號", key="r_u").lower()
            r_p = st.text_input("密碼", type="password", key="r_p")
            if st.button("完成註冊"):
                success, msg = handle_auth("註冊", r_u, r_p)
                if success: st.success(msg)
                else: st.error(msg)
    else:
        st.write(f"👤 當前使用者: {st.session_state.username}")
        if st.button("安全登出"):
            st.session_state.auth_status = False
            st.session_state.username = "guest"
            st.rerun()

    st.divider()
    menu = ["市場看盤"]
    if st.session_state.auth_status:
        menu += ["個人策略設定", "投資組合管理"]
    choice = st.radio("功能導覽", menu)

    st.divider()
    st.subheader("圖表設定")
    period = st.selectbox("時間範圍", ["1mo", "3mo", "6mo", "1y", "5y"], index=2)
    show_ma = st.multiselect("顯示均線", ["MA10", "MA20", "MA50", "MA200"], default=["MA20", "MA50"])
    show_bb = st.checkbox("顯示布林通道", value=True)

    if st.session_state.auth_status:
        st.divider()
        st.subheader("我的觀察清單")
        new_t = st.text_input("新增代碼", placeholder="例如: TSLA").upper().strip()
        if st.button("確認新增"):
            if new_t:
                _PM.add_transaction(st.session_state.username, new_t, "watchlist", datetime.now().strftime("%Y-%m-%d"), 0, 0)
                st.success(f"{new_t} 已加入")
                st.rerun()

        watchlist = _PM.get_watchlist(st.session_state.username)
        if watchlist:
            pick = st.selectbox("快速切換標的", ["-- 選擇 --"] + watchlist)
            if pick != "-- 選擇 --":
                st.session_state.current_ticker = pick

# --- 5. 功能頁面實作 ---

def render_market_dashboard():
    """市場看盤與 AI 分析診斷"""
    username = st.session_state.username
    st.header("📈 市場分析看板")
    
    if st.session_state.auth_status:
        watchlist = _PM.get_watchlist(username)
        price_map = {}
        if watchlist:
            with st.spinner("正在更新資產總覽..."):
                for t in watchlist:
                    df_temp, _ = get_stock_data(t, username, force_refresh=False)
                    if df_temp is not None and not df_temp.empty:
                        price_map[t] = {
                            "current": df_temp['Close'].iloc[-1],
                            "prev_close": df_temp['Close'].iloc[-2] if len(df_temp) > 1 else df_temp['Close'].iloc[-1]
                        }
        
        summary = _PM.get_portfolio_summary(username, price_map)
        g1, g2, g3 = st.columns(3)
        g1.metric("資產總市值", f"${summary['total_market_value']:,.2f}")
        g2.metric("未實現損益", f"${summary['total_unrealized_pnl']:,.2f}", delta=f"{summary['total_roi_pct']:.2f}%")
        g3.metric("已實現損益", f"${summary['total_realized_pnl']:,.2f}")
        st.divider()

    ticker = st.text_input("輸入股票代碼", value=st.session_state.current_ticker).upper()
    st.session_state.current_ticker = ticker
    df_raw, distilled = get_stock_data(ticker, username)
    
    if df_raw is not None:
        df = _filter_df_by_period(df_raw, period)
        current_p = float(df['Close'].iloc[-1])
        st.subheader(f"標的分析: {ticker}")
        m1, m2, m3 = st.columns(3)
        m1.metric("代碼", ticker)
        m2.metric("當前市價", f"${current_p:,.2f}")
        m3.metric("最後資料日期", df.index.max().strftime("%Y-%m-%d"))

        fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線")])
        colors = {"MA10": "#ffeb3b", "MA20": "#ff9800", "MA50": "#9c27b0", "MA200": "#00e5ff"}
        for ma in show_ma:
            if ma in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=colors.get(ma), width=1.2)))
        if show_bb and "BB_upper" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_upper"], name="布林上軌", line=dict(color="rgba(173, 216, 230, 0.4)", dash="dash")))
            fig.add_trace(go.Scatter(x=df.index, y=df["BB_lower"], name="布林下軌", line=dict(color="rgba(173, 216, 230, 0.4)", dash="dash"), fill='tonexty'))
        
        fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

        # --- 關鍵修正：美化的持股診斷面板 ---
        st.subheader(f"{ticker} 持股診斷")
        pm_info = {ticker: {"current": current_p, "prev_close": df['Close'].iloc[-2] if len(df)>1 else current_p}}
        user_summary = _PM.get_portfolio_summary(username, pm_info)
        active_holding = [h for h in user_summary["holdings"] if h["ticker"] == ticker]
        
        if not active_holding:
            # 建立空數據結構
            active_holding = [{
                "ticker": ticker, "total_shares": 0, "avg_cost": 0, "market_value": 0, 
                "inventory_cost": 0, "cumulative_buy_cost": 0, "unrealized_pnl": 0, 
                "realized_pnl": 0, "roi_pct": 0, "day_change_amt": 0, "day_change_pct": 0
            }]
        
        h_df = pd.DataFrame(active_holding)
        # 套用自定義格式與顏色邏輯
        st.dataframe(format_portfolio_df(h_df), use_container_width=True, hide_index=True)

        if st.session_state.auth_status:
            st.divider()
            strategy = _ARCHIVE.load_strategy(username)
            if strategy.get("gemini_api_key"):
                if st.button("🚀 啟動 AI 客製化診斷"):
                    with st.spinner("AI 分析中..."):
                        p_metrics = _PM.calculate_metrics(username, ticker, current_p)
                        report = _ANALYST.run_deep_analysis(username, ticker, distilled, p_metrics, api_key=strategy['gemini_api_key'])
                        _ARCHIVE.save_text(username, "reports", "analysis_report.md", report, ticker=ticker, date=datetime.now())
                        st.rerun()
                
                report_md = _ARCHIVE.load_text(username, "reports", "analysis_report.md", ticker=ticker, date=datetime.now())
                if report_md:
                    with st.expander("📄 查看 AI 深度報告", expanded=True):
                        st.markdown(report_md)

def render_strategy_settings():
    """個人投資策略設定頁面"""
    st.header("⚙️ 個人投資策略設定")
    username = st.session_state.username
    current_strategy = _ARCHIVE.load_strategy(username)
    with st.form("strategy_editor"):
        st.subheader("1. 投資性格與偏好")
        col1, col2, col3 = st.columns(3)
        with col1:
            risk = st.selectbox("風險承受度", ["低", "一般", "高"], index=["低", "一般", "高"].index(current_strategy.get('risk_tolerance', '一般')))
        with col2:
            style = st.selectbox("交易風格", ["保守", "一般", "激進"], index=["保守", "一般", "激進"].index(current_strategy.get('trading_style', '一般')))
        with col3:
            freq = st.selectbox("交易頻率", ["短線", "長期"], index=["短線", "長期"].index(current_strategy.get('trading_frequency', '長期')))
        st.divider()
        st.subheader("2. AI 密鑰注入 (Gemini API)")
        api_key = st.text_input("Gemini API Key", value=current_strategy.get('gemini_api_key', ''), type="password")
        if st.form_submit_button("儲存並套用策略"):
            new_strategy = {"risk_tolerance": risk, "trading_style": style, "trading_frequency": freq, "gemini_api_key": api_key}
            _ARCHIVE.save_strategy(username, new_strategy)
            st.success("策略已更新！")

def render_portfolio_management():
    """投資組合管理 (WAC 算法)"""
    st.header("💼 投資組合管理")
    username = st.session_state.username
    with st.expander("➕ 新增買賣紀錄", expanded=False):
        with st.form("tx_input", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            t_ticker = c1.text_input("代碼").upper()
            t_type = c2.selectbox("類型", ["buy", "sell"])
            t_date = c3.date_input("日期")
            t_price = c1.number_input("成交單價", min_value=0.01)
            t_shares = c2.number_input("成交股數", min_value=0.01)
            if st.form_submit_button("確認提交"):
                if t_ticker:
                    _PM.add_transaction(username, t_ticker, t_type, t_date.strftime("%Y-%m-%d"), t_price, t_shares)
                    st.success("紀錄已儲存")
                    st.rerun()

    st.subheader("當前持倉明細 (加權平均成本)")
    watchlist = _PM.get_watchlist(username)
    if watchlist:
        price_map = {}
        for t in watchlist:
            df_temp, _ = get_stock_data(t, username, force_refresh=False)
            if df_temp is not None and not df_temp.empty:
                price_map[t] = {"current": df_temp['Close'].iloc[-1], "prev_close": df_temp['Close'].iloc[-2] if len(df_temp)>1 else df_temp['Close'].iloc[-1]}
        
        summary = _PM.get_portfolio_summary(username, price_map)
        if summary["holdings"]:
            # 同樣對管理分頁的表格套用美化
            st.dataframe(format_portfolio_df(pd.DataFrame(summary["holdings"])), use_container_width=True, hide_index=True)
        else:
            st.info("目前尚無庫存數據。")
    else:
        st.info("請先在側邊欄新增觀察標的。")

# --- 6. 主程式路由控制 ---
if choice == "市場看盤":
    render_market_dashboard()
elif choice == "個人策略設定":
    render_strategy_settings()
elif choice == "投資組合管理":
    render_portfolio_management()