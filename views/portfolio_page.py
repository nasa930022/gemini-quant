"""
views/portfolio_page.py - 投資組合管理頁面
Phase 2 重構：從 main.py 分離
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from views.market import format_portfolio_df


def render_portfolio_management(archive, portfolio_mgr):
    """渲染投資組合管理頁面。"""
    st.header("💼 投資組合管理")
    username = st.session_state.username

    with st.expander("➕ 新增買賣紀錄"):
        with st.form("tx_input", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            t_ticker = c1.text_input("代碼").upper()
            t_type = c2.selectbox("類型", ["buy", "sell"])
            t_date = c3.date_input("日期")
            t_price = c1.number_input("成交單價", min_value=0.01)
            t_shares = c2.number_input("成交股數", min_value=0.01)
            if st.form_submit_button("提交"):
                if t_ticker:
                    portfolio_mgr.add_transaction(
                        username, t_ticker, t_type,
                        t_date.strftime("%Y-%m-%d"), t_price, t_shares
                    )
                    st.rerun()

    tracked = portfolio_mgr.get_tracked_tickers(username)
    if tracked:
        from views.market import _cached_get_latest_prices
        price_map = _cached_get_latest_prices(tuple(tracked))
        summary = portfolio_mgr.get_portfolio_summary(username, price_map)
        if summary["holdings"]:
            st.dataframe(
                format_portfolio_df(pd.DataFrame(summary["holdings"])),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("目前無持倉紀錄，請透過上方表單新增買賣記錄。")
