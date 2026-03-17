"""
股票數據處理模組：從 yfinance 抓取數據，計算技術指標並進行多尺度蒸餾。
優化版：支援輕量化價格查詢與數據快取機制。
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import pandas_ta as ta
import yfinance as yf

from utils import ArchiveManager

# yfinance 正確的 period 格式
_PERIOD_MAP = {"1m": "1mo", "3m": "3mo", "6m": "6mo", "1y": "1y", "5y": "5y"}
_ARCHIVE = ArchiveManager()

def get_price_info(ticker: str) -> dict:
    """
    輕量化價格獲取：僅用於資產總覽。
    不計算技術指標，不寫入檔案，僅回傳當前市價與昨收價。
    """
    try:
        # 僅抓取 5 天數據以確保能拿到最後兩個交易日的收盤價
        data = yf.download(ticker, period="5d", interval="1d", progress=False, threads=False)
        if data.empty:
            return {"current": 0.0, "prev_close": 0.0}
        
        # 處理 MultiIndex 並取得最後兩筆收盤價
        close_series = data['Close']
        if isinstance(close_series, pd.DataFrame):
            close_series = close_series.iloc[:, 0]
            
        current_price = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2]) if len(close_series) > 1 else current_price
        
        return {"current": current_price, "prev_close": prev_close}
    except Exception:
        return {"current": 0.0, "prev_close": 0.0}

def get_stock_data(
    ticker: str,
    period: str = "6mo",
    force_refresh: bool = True
) -> Tuple[Optional[pd.DataFrame], Optional[str], Optional[float]]:
    """
    抓取完整數據並計算指標。
    force_refresh=True: 用於當前分析個股，必抓最新。
    force_refresh=False: 用於背景計算，若今日已有蒸餾 JSON 則直接返回，不重複抓取。
    """
    ui_period = _PERIOD_MAP.get(period, period)
    as_of_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"distilled_{ui_period}"

    # 非強制更新時，先檢查本地快取
    if not force_refresh:
        cached_distilled = _ARCHIVE.load_json(ticker, as_of_str, filename)
        if cached_distilled:
            # 注意：這裡只返回 None 作為 DF，因為背景計算通常不需要完整圖表 DF
            # 若需要 DF 則需另存 raw_data.csv，此處簡化處理為「僅在分析時抓取 DF」
            return None, ticker, None

    try:
        # 抓取 1 年數據以確保 MA200 等長期指標
        df = yf.download(
            ticker,
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )

        if df is None or df.empty:
            return None, f"無法取得 {ticker} 的數據", None

        df = _flatten_columns(df)
        if "Close" not in df.columns:
            return None, "數據欄位錯誤", None

        # 計算技術指標
        df = compute_indicators(df)

        # 獲取名稱與現價
        stock = yf.Ticker(ticker)
        # 為了效能，優先從 DF 拿最後一筆，不呼叫昂貴的 stock.info
        name = ticker 
        current_price = float(df["Close"].iloc[-1])

        # 執行數據蒸餾並落地
        distilled = get_distilled_data(df)
        _ARCHIVE.save_json(ticker, df.index[-1].strftime("%Y-%m-%d"), filename, distilled)

        return df, name, current_price

    except Exception as e:
        return None, str(e), None

def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    df = df.copy()
    for i in range(df.columns.nlevels):
        lev = df.columns.get_level_values(i)
        if "Close" in lev:
            df.columns = lev
            return df
    df.columns = df.columns.get_level_values(0)
    return df

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    df["MA10"] = ta.sma(close, length=10)
    df["MA20"] = ta.sma(close, length=20)
    df["MA50"] = ta.sma(close, length=50)
    df["MA200"] = ta.sma(close, length=200)
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None:
        df["BB_lower"] = bb.iloc[:, 0]
        df["BB_mid"] = bb.iloc[:, 1]
        df["BB_upper"] = bb.iloc[:, 2]
    df["RSI14"] = ta.rsi(close, length=14)
    return df

def compute_summary_metrics(df: pd.DataFrame) -> dict:
    if df is None or df.empty: return {}
    last = df.iloc[-1]
    d1m = df.tail(21)
    d3m = df.tail(63)
    return {
        "今日開盤價": last["Open"],
        "今日股價範圍": f"{last['Low']:.2f} - {last['High']:.2f}",
        "震幅 (%)": round((last["High"] - last["Low"]) / last["Open"] * 100, 2) if last["Open"] else 0,
        "一個月股價範圍": f"{d1m['Low'].min():.2f} - {d1m['High'].max():.2f}",
        "三個月股價範圍": f"{d3m['Low'].min():.2f} - {d3m['High'].max():.2f}",
    }

def get_distilled_data(df: pd.DataFrame) -> dict:
    if df is None or df.empty: return {}
    df = df.dropna(subset=["Close"])
    last = df.iloc[-1]
    
    # Macro
    d52 = df.tail(252)
    ma200 = float(last["MA200"]) if "MA200" in last and not pd.isna(last["MA200"]) else 0
    macro = {
        "52w_high": float(d52["High"].max()),
        "52w_low": float(d52["Low"].min()),
        "price_vs_ma200_pct": ((float(last["Close"]) - ma200) / ma200 * 100) if ma200 else 0,
    }

    # Meso
    ma_order = "混合"
    if last.get("MA10") > last.get("MA20") > last.get("MA50"): ma_order = "多頭排列"
    elif last.get("MA10") < last.get("MA20") < last.get("MA50"): ma_order = "空頭排列"
    
    meso = {
        "ma_order": ma_order,
        "rsi_last": float(last.get("RSI14", 0)),
    }

    # Micro
    d5 = df.tail(5)
    micro = {"recent_5d_close": d5["Close"].tolist()}

    return {"macro": macro, "meso": meso, "micro": micro}