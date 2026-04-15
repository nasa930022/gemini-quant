"""
main.py - Project Gemini-Quant v4.0 (Multi-User Edition)
架構重構版：
- 全域初始化、CSS、Session State、側邊欄、路由邏輯保留於此
- 各功能頁面邏輯已拆分至 pages/ 子模組
"""

import streamlit as st
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# 自動載入 .env 中的環境變數 (包括 GEMINI_API_KEY)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)

# 載入自定義模組
from utils.archive import ArchiveManager, get_archive, hash_password, verify_password
from utils.portfolio import PortfolioManager
from utils.analyst import Analyst
from views.market import render_market_dashboard
from views.strategy import render_strategy_settings
from views.portfolio_page import render_portfolio_management

# --- 1. 基礎設定與實例初始化 ---
st.set_page_config(page_title="Gemini-Quant v4.0", layout="wide", initial_sidebar_state="expanded")

_ARCHIVE = get_archive()
_PM = PortfolioManager(_ARCHIVE)
_ANALYST = Analyst(_ARCHIVE)
AUTH_FILE = Path("storage/auth.json")

# 自定義 CSS
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; }
    .stMetric { background-color: #1e1e2f; padding: 10px; border-radius: 10px; border: 1px solid #303030; }
    h1, h2, h3, p, label { color: #e0e0e0 !important; }
    .stDataFrame { background-color: #1e1e2f; border-radius: 10px; }
    .stButton>button { width: 100%; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)


# --- 2. 身份驗證 ---

def handle_auth(mode, user, pw):
    if not AUTH_FILE.exists():
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTH_FILE.write_text("{}", encoding="utf-8")
    auth_data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    user = user.strip().lower()
    if mode == "註冊":
        if not user or not pw:
            return False, "欄位不可為空"
        if user in auth_data:
            return False, "此帳號已存在"
        auth_data[user] = hash_password(pw)  # 存儲雜湊密碼，非明文
        AUTH_FILE.write_text(json.dumps(auth_data), encoding="utf-8")
        _ARCHIVE.get_user_dir(user)
        return True, "註冊成功，請登入"
    elif mode == "登入":
        stored = auth_data.get(user)
        if stored and verify_password(pw, stored):
            return True, "登入成功"
        return False, "帳號或密碼錯誤"
    return False, "錯誤"


# --- 3. Session State 初始化 ---
if "auth_status" not in st.session_state:
    st.session_state.auth_status = False
    st.session_state.username = "guest"
if "current_ticker" not in st.session_state:
    st.session_state.current_ticker = "NVDA"
if "view_history" not in st.session_state:
    st.session_state.view_history = False


# --- 4. 側邊欄 ---
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
                else:
                    st.error(msg)
        with auth_tab[1]:
            r_u = st.text_input("帳號", key="r_u").lower()
            r_p = st.text_input("密碼", type="password", key="r_p")
            if st.button("完成註冊"):
                success, msg = handle_auth("註冊", r_u, r_p)
                if success:
                    st.success(msg)
                else:
                    st.error(msg)
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
                from datetime import datetime
                _PM.add_transaction(
                    st.session_state.username, new_t, "watchlist",
                    datetime.now().strftime("%Y-%m-%d"), 0, 0
                )
                st.success(f"{new_t} 已加入")
                st.rerun()
        watchlist = _PM.get_watchlist(st.session_state.username)
        if watchlist:
            pick = st.selectbox("快速切換標的", ["-- 選擇 --"] + watchlist)
            if pick != "-- 選擇 --":
                st.session_state.current_ticker = pick


# --- 5. 頁面路由 ---
if choice == "市場看盤":
    render_market_dashboard(_ARCHIVE, _PM, _ANALYST, period, show_ma, show_bb)
elif choice == "個人策略設定":
    render_strategy_settings(_ARCHIVE)
elif choice == "投資組合管理":
    render_portfolio_management(_ARCHIVE, _PM)


