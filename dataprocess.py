"""
dataprocess.py - Project Gemini-Quant v4.0 (Personalized Edition)
功能：
1. 共享數據層 (Shared Layer)：中心化儲存原始歷史 CSV，節省 API 額度。
2. 冷熱數據分離：保護歷史資料不被更動，僅動態抓取並合併今日即時走勢。
3. 個人化蒸餾 (Personalized Distillation)：
   - 風險導向：針對不同風險承受度計算回撤與波動率。
   - 風格分流：為「一般」風格提供平衡指標，為「激進/保守」提供專屬訊號。
"""

import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

import concurrent.futures
import pandas as pd
import pandas_ta as ta
import yfinance as yf

from utils.archive import get_archive

# 初始化日誌與存儲管理
logger = logging.getLogger(__name__)
_ARCHIVE = get_archive()

def get_latest_prices(tickers: list) -> dict:
    """極速獲取多檔標的當前價格與昨日收盤價。專門用於優化儀表板效能，不計算技術指標。"""
    if not tickers: return {}
    result = {}
    
    def fetch(t):
        try:
            df = yf.Ticker(t).history(period="5d")
            if not df.empty and "Close" in df.columns:
                s = df["Close"].dropna()
                if len(s) >= 2:
                    return t, {"current": float(s.iloc[-1]), "prev_close": float(s.iloc[-2])}
                elif len(s) == 1:
                    return t, {"current": float(s.iloc[-1]), "prev_close": float(s.iloc[-1])}
        except Exception as e:
            logger.warning(f"獲取 {t} 最新報價失敗: {e}")
        return t, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(tickers))) as executor:
        for t, data in executor.map(fetch, tickers):
            if data:
                result[t] = data
    return result

def get_stock_data(
    ticker: str,
    username: str,
    force_refresh: bool = False
) -> Tuple[Optional[pd.DataFrame], Optional[Dict]]:
    """
    主進入點：獲取合併數據、計算指標並執行個人化蒸餾。
    """
    ticker = ticker.upper()
    
    # 1. 獲取合併後的完整數據 (歷史 + 今日即時)
    df = _fetch_and_merge_data(ticker, force_refresh)
    if df is None or df.empty:
        logger.error(f"無法取得 {ticker} 的數據")
        return None, None

    # 2. 計算標準技術指標 (中心化計算，供所有使用者共用)
    df = compute_indicators(df)

    # 3. 獲取使用者個人策略 (從 storage/users/{username}/profiles/strategy.json)
    strategy = _ARCHIVE.load_strategy(username)

    # 4. 執行個人化蒸餾 (根據使用者風險與風格裁切數據)
    distilled = get_personalized_distillation(df, strategy)

    # 5. 存儲蒸餾結果到使用者私有快取 (storage/users/{username}/cache/)
    _ARCHIVE.save_json(
        username=username,
        category="cache",
        filename=f"{ticker}_distilled",
        data=distilled
    )

    return df, distilled

def _fetch_and_merge_data(ticker: str, force_refresh: bool) -> Optional[pd.DataFrame]:
    """
    冷熱分離邏輯：
    - 冷數據：讀取 shared/raw_data/{ticker}/history.csv
    - 熱數據：抓取今日最新 1d 數據
    - 優點：不動先前資料，確保歷史數據穩定性。
    """
    shared_dir = _ARCHIVE.get_shared_path("raw_data", ticker)
    history_file = shared_dir / "history.csv"
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # A. 讀取或初始化歷史數據
    df_hist = pd.DataFrame()
    if history_file.exists() and not force_refresh:
        try:
            df_hist = pd.read_csv(history_file, index_col=0, parse_dates=True)
            # 確保歷史數據不包含「未收盤」的今日
            df_hist = df_hist[df_hist.index < today_str]
        except Exception as e:
            logger.warning(f"歷史檔案讀取失敗，將重新抓取: {e}")

    # B. 若無歷史數據，抓取過去兩年並存檔
    if df_hist.empty:
        logger.info(f"正在建立 {ticker} 的中心化歷史數據庫...")
        df_hist = yf.download(ticker, period="2y", interval="1d", progress=False)
        df_hist = _flatten_columns(df_hist)
        if not df_hist.empty:
            df_hist.to_csv(history_file)
        return df_hist

    # C. 抓取近期熱數據 (從歷史紀錄最後一天到今日)
    try:
        last_date = df_hist.index.max()
        # 修正：不只抓 1d，而是抓取從歷史結尾後的所有缺口數據
        # yfinance 的 start 是 inclusive，我們在 concat 時處理重疊即可
        df_recent = yf.download(ticker, start=last_date, interval="1d", progress=False)
        df_recent = _flatten_columns(df_recent)
        
        if not df_recent.empty:
            # 移除歷史中重疊的日期，確保無缝拼接且數據唯一
            df_hist = df_hist[df_hist.index < df_recent.index[0]]
            df_final = pd.concat([df_hist, df_recent])

            # --- 優化策略：若歷史檔案過舊 (例如超過 3 天沒更新)，更新中心化歷史庫 ---
            from datetime import timedelta
            if (datetime.now() - last_date).days > 3:
                logger.info(f"正在更新 {ticker} 的歷史數據庫快取...")
                # 將「已收盤」的數據存回歷史檔案，今日「未收盤」的不存 (以確保下次抓取仍是熱數據)
                df_to_save = df_final[df_final.index < today_str]
                df_to_save.to_csv(history_file)
                
            return df_final
    except Exception as e:
        logger.warning(f"無法獲取近期即時數據: {e}")
        return df_hist

    return df_hist

def get_personalized_distillation(df: pd.DataFrame, strategy: dict) -> dict:
    """
    個人化蒸餾核心邏輯：
    1. 針對「一般」風格提供黃金平衡指標。
    2. 整合「風險承受度」進行最大回撤與安全性分析。
    """
    horizon = strategy.get("trading_frequency", "長期")
    style = strategy.get("trading_style", "一般")
    risk = strategy.get("risk_tolerance", "一般")
    
    # --- 修正：安全處理空值，避免因 yfinance 開盤前空數據導致 NaN ---
    df_valid = df.dropna(subset=["Close"])
    if df_valid.empty: return {}
    last = df_valid.iloc[-1]
    
    # --- 1. 全域風險量化 (Risk Metrics) ---
    rolling_max = df["Close"].rolling(window=252, min_periods=1).max()
    drawdown = (df["Close"] - rolling_max) / rolling_max
    current_dd = float(drawdown.iloc[-1])

    distilled = {
        "user_context": {"horizon": horizon, "style": style, "risk": risk},
        "price_info": {
            "current": float(last["Close"]), 
            "date": df.index[-1].strftime("%Y-%m-%d")
        },
        "risk_assessment": {
            "max_drawdown_252d_pct": round(current_dd * 100, 2),
            "risk_status": "警告：處於高回撤區間" if current_dd < -0.20 else "正常"
        }
    }

    # 根據「風險承受度」深化數據
    if risk == "低":
        ma200 = last.get("MA200")
        safety_margin = 0.0
        if pd.notna(ma200) and ma200 > 0:
            safety_margin = round(((float(last["Close"]) - float(ma200)) / float(ma200)) * 100, 2)
            
        distilled["risk_assessment"].update({
            "ann_volatility": round(float(df["Close"].pct_change().tail(252).std() * (252**0.5)) * 100, 2),
            "safety_margin_ma200": safety_margin
        })
    elif risk == "高":
        distilled["risk_assessment"].update({
            "upside_potential_to_52w_high": round(((df["High"].tail(252).max() - float(last["Close"])) / float(last["Close"])) * 100, 2)
        })

    # --- 2. 風格導向特徵 (Strategy Focus) ---
    view_days = 20 if horizon == "短線" else 252
    sub_df = df.tail(view_days)

    if style == "激進":
        bb_upper = last.get("BB_upper")
        bb_lower = last.get("BB_lower")
        bb_position = 0.5
        if pd.notna(bb_upper) and pd.notna(bb_lower) and float(bb_upper) != float(bb_lower):
            bb_position = round((float(last["Close"]) - float(bb_lower)) / (float(bb_upper) - float(bb_lower)), 2)
            
        distilled["strategy_focus"] = {
            "mode": "Momentum_Aggressive",
            "rsi14": round(float(last["RSI14"]), 2) if pd.notna(last.get("RSI14")) else "N/A",
            "bb_position": bb_position,
            "ma_trend_short": "Strong_Up" if last["MA10"] > last["MA20"] else "Correcting"
        }
    elif style == "保守":
        distilled["strategy_focus"] = {
            "mode": "Stability_Conservative",
            "above_ma200": bool(last["Close"] > last["MA200"]),
            "volume_health": "Stable" if last["Volume"] > df["Volume"].tail(20).mean() else "Low_Volume",
            "ma_order": "Bullish_Array" if last["MA50"] > last["MA200"] else "Neutral"
        }
    else:
        # --- 安全處理 RSI 比較 ---
        rsi_val = last.get("RSI14")
        if pd.isna(rsi_val):
            rsi_status = "Unknown"
        else:
            rsi_status = "Neutral" if 40 <= rsi_val <= 60 else ("Overbought" if rsi_val > 60 else "Oversold")

        # --- 修正區塊：安全處理均線比較 (MA20 vs MA50) ---
        ma20 = last.get("MA20")
        ma50 = last.get("MA50")
        
        if pd.notna(ma20) and pd.notna(ma50):
            trend_status = "Bullish" if ma20 > ma50 else "Neutral"
        else:
            trend_status = "Unknown" # 數據不足無法判斷趨勢

        distilled["strategy_focus"] = {
            "mode": "Balanced_Neutral",
            "rsi_stability": rsi_status,
            "price_range_pos": round((float(last["Close"]) - sub_df["Low"].min()) / (sub_df["High"].max() - sub_df["Low"].min()), 2),
            "trend_alignment": trend_status
        }

    return distilled

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    計算標準技術指標：MA10, 20, 50, 200, BBands, RSI。
    """
    df = df.copy()
    close = df["Close"]
    
    # 均線
    df["MA10"] = ta.sma(close, length=10)
    df["MA20"] = ta.sma(close, length=20)
    df["MA50"] = ta.sma(close, length=50)
    df["MA200"] = ta.sma(close, length=200)
    
    # 布林通道
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None:
        df["BB_lower"] = bb.iloc[:, 0]
        df["BB_mid"] = bb.iloc[:, 1]
        df["BB_upper"] = bb.iloc[:, 2]
        
    # 強弱指標
    df["RSI14"] = ta.rsi(close, length=14)
    
    return df

def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """處理 yfinance 在多標的或新版本中產生的 MultiIndex 欄位。"""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    df.columns = df.columns.get_level_values(0)
    return df