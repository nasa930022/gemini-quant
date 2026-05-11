"""
views/portfolio_page.py - 投資組合管理頁面
Phase 2 重構：從 main.py 分離
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from views.market import format_portfolio_df


def render_portfolio_management(archive, portfolio_mgr, analyst=None):
    """渲染投資組合管理頁面。"""
    st.header("💼 投資組合管理")
    username = st.session_state.username

    import os
    strategy = archive.load_strategy(username)
    api_key = strategy.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")

    with st.expander("➕ 新增買賣紀錄 (支援截圖與批量匯入)", expanded=False):
        tab1, tab2, tab3 = st.tabs(["單筆輸入", "截圖智慧建檔 (推薦)", "批量文字匯入"])
        
        with tab1:
            with st.form("tx_input", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                t_ticker = c1.text_input("代碼").upper()
                t_type = c2.selectbox("類型", ["buy", "sell"])
                t_date = c3.date_input("日期")
                t_price = c1.number_input("成交單價", min_value=0.01)
                t_shares = c2.number_input("成交股數", min_value=0.01)
                if st.form_submit_button("提交單筆紀錄"):
                    if t_ticker:
                        portfolio_mgr.add_transaction(
                            username, t_ticker, t_type,
                            t_date.strftime("%Y-%m-%d"), t_price, t_shares
                        )
                        st.success(f"已新增 {t_ticker} 紀錄")
                        st.rerun()
                        
        with tab2:
            st.info("上傳券商 APP 的「庫存總覽」截圖，Gemini 會自動幫你辨識股票代碼、平均成本與股數。")
            uploaded_img = st.file_uploader("上傳持股截圖 (PNG/JPG)", type=["png", "jpg", "jpeg"])
            if uploaded_img and analyst:
                if st.button("🔍 開始辨識圖片"):
                    with st.spinner("Gemini 視覺模型解析中..."):
                        try:
                            parsed_data = analyst.parse_portfolio_image(
                                uploaded_img.read(), uploaded_img.type, api_key=api_key
                            )
                            st.session_state["parsed_portfolio"] = parsed_data
                        except Exception as e:
                            st.error(f"辨識失敗：{e}")
            
        with tab3:
            st.info("貼上你的投資組合清單（例如從 Excel 複製的代碼、價格、數量），不限格式，AI 會自動推論。")
            pasted_text = st.text_area("貼上投資組合資料", height=150)
            if pasted_text and analyst:
                if st.button("🔍 解析文字資料"):
                    with st.spinner("Gemini 文字解析中..."):
                        try:
                            parsed_data = analyst.parse_portfolio_text(pasted_text, api_key=api_key)
                            st.session_state["parsed_portfolio"] = parsed_data
                        except Exception as e:
                            st.error(f"解析失敗：{e}")

        # 如果有解析成功的預覽資料，顯示預覽表格並提供確認寫入按鈕
        if "parsed_portfolio" in st.session_state and st.session_state["parsed_portfolio"]:
            st.divider()
            st.subheader("👀 辨識結果預覽")
            preview_df = pd.DataFrame(st.session_state["parsed_portfolio"])
            st.dataframe(preview_df, use_container_width=True)
            
            if st.button("✅ 確認寫入所有紀錄 (以今日為購買日)", type="primary"):
                today_str = datetime.now().strftime("%Y-%m-%d")
                count = 0
                for row in st.session_state["parsed_portfolio"]:
                    ticker = str(row.get("ticker", "")).upper()
                    price = float(row.get("price", 0))
                    shares = float(row.get("shares", 0))
                    
                    if ticker and price > 0 and shares > 0:
                        portfolio_mgr.add_transaction(
                            username, ticker, "buy", today_str, price, shares
                        )
                        count += 1
                
                st.session_state.pop("parsed_portfolio")
                st.success(f"成功大量寫入 {count} 筆紀錄！")
                st.rerun()
        elif "parsed_portfolio" in st.session_state and not st.session_state["parsed_portfolio"]:
             st.warning("找不到任何符合的股票紀錄，請確認圖片或文字內容。")
             if st.button("清除結果"):
                 st.session_state.pop("parsed_portfolio")
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
