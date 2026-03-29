"""
pages/strategy.py - 個人投資策略設定頁面
Phase 2 重構：從 main.py 分離
"""

import streamlit as st


def render_strategy_settings(archive):
    """渲染個人投資策略設定頁面。"""
    st.header("⚙️ 個人投資策略設定")
    username = st.session_state.username
    current_strategy = archive.load_strategy(username)

    with st.form("strategy_editor"):
        col1, col2, col3 = st.columns(3)
        with col1:
            risk = st.selectbox(
                "風險承受度", ["低", "一般", "高"],
                index=["低", "一般", "高"].index(current_strategy.get('risk_tolerance', '一般'))
            )
        with col2:
            style = st.selectbox(
                "交易風格", ["保守", "一般", "激進"],
                index=["保守", "一般", "激進"].index(current_strategy.get('trading_style', '一般'))
            )
        with col3:
            freq = st.selectbox(
                "交易頻率", ["短線", "長期"],
                index=["短線", "長期"].index(current_strategy.get('trading_frequency', '長期'))
            )
        api_key = st.text_input(
            "Gemini API Key（可留空，將自動讀取 .env 中的 GEMINI_API_KEY）",
            value=current_strategy.get('gemini_api_key', ''),
            type="password"
        )
        if st.form_submit_button("儲存策略"):
            archive.save_strategy(username, {
                "risk_tolerance": risk,
                "trading_style": style,
                "trading_frequency": freq,
                "gemini_api_key": api_key
            })
            st.success("策略已更新")
