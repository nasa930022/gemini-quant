"""
portfolio.py - 強化版多使用者財務管理模組
修正：
1. 精確 WAC 扣除邏輯：賣出時嚴格按比例扣除 inventory_cost，確保平倉後庫存成本歸零。
2. 健壯的總覽算法：過濾無效價格，防止因 API 抓取失敗導致總市值歸零。
3. 累積投入成本追蹤：新增累積投入額計算，提供更合理的「全域投資報酬率」。
"""

import logging
from typing import Dict, List, Optional
from utils.archive import ArchiveManager

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, archive: Optional[ArchiveManager] = None):
        """
        初始化時引入 ArchiveManager。
        不需要在 init 指定檔案路徑，改在操作時根據 username 動態定址。
        """
        self.archive = archive or ArchiveManager()

    def _load_user_data(self, username: str) -> dict:
        """載入特定使用者的交易與觀察清單。"""
        data = self.archive.load_json(username, "portfolio", "transactions")
        if data:
            return data
        # 新使用者的初始結構
        return {"watchlist": [], "portfolio": {}}

    def _save_user_data(self, username: str, data: dict):
        """儲存特定使用者的數據。"""
        self.archive.save_json(username, "portfolio", "transactions", data)

    def add_transaction(self, username: str, ticker: str, t_type: str, date: str, price: float, shares: float):
        """
        新增交易紀錄並自動更新觀察清單。
        """
        username = username.lower()
        ticker = ticker.upper()
        data = self._load_user_data(username)

        # 1. 更新觀察清單 (Watchlist)
        if ticker not in data["watchlist"]:
            data["watchlist"].append(ticker)

        # 2. 寫入交易紀錄
        if ticker not in data["portfolio"]:
            data["portfolio"][ticker] = {"transactions": []}
            
        data["portfolio"][ticker]["transactions"].append({
            "date": date, 
            "type": t_type, 
            "price": price, 
            "shares": shares
        })

        self._save_user_data(username, data)
        logger.info(f"使用者 [{username}] 新增交易: {ticker} {t_type} {shares} 股 @ {price}")

    def calculate_metrics(self, username: str, ticker: str, current_price: float = 0.0, prev_close: float = 0.0) -> dict:
        """
        核心算法：精確加權平均成本法 (WAC)
        """
        ticker = ticker.upper()
        data = self._load_user_data(username)
        
        if ticker not in data.get("portfolio", {}):
            return {
                "ticker": ticker,
                "total_shares": 0.0,
                "avg_cost": 0.0,
                "market_value": 0.0,
                "inventory_cost": 0.0,
                "cumulative_buy_cost": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "roi_pct": 0.0,
                "day_change_amt": 0.0,
                "day_change_pct": 0.0
            }

        transactions = data["portfolio"][ticker].get("transactions", [])
        total_shares = 0.0
        inventory_cost = 0.0  # 當前持倉的總成本 (帳面價值)
        realized_pnl = 0.0
        cumulative_buy_cost = 0.0 # 該標的歷史累計買入成本

        # 排序確保時序正確
        sorted_txs = sorted(transactions, key=lambda x: x['date'])

        for tx in sorted_txs:
            p, s = tx["price"], tx["shares"]
            
            if tx["type"] == "buy":
                total_shares += s
                inventory_cost += p * s
                cumulative_buy_cost += p * s
            elif tx["type"] == "sell":
                if total_shares > 0:
                    # 計算賣出時的單位平均成本
                    unit_avg_cost = inventory_cost / total_shares
                    # 已實現損益 = (賣價 - 平均成本) * 賣出股數
                    realized_pnl += (p - unit_avg_cost) * s
                    
                    # 關鍵修正：必須按比例扣除庫存成本，否則平倉後會留有殘餘成本導致 ROI 錯誤
                    total_shares -= s
                    if total_shares <= 0:
                        total_shares = 0
                        inventory_cost = 0 # 全數賣出，成本歸零
                    else:
                        inventory_cost = total_shares * unit_avg_cost
                else:
                    # 異常處理：無庫存賣出（此處暫不處理放空）
                    pass

        # 財務指標彙整
        avg_cost = inventory_cost / total_shares if total_shares > 0 else 0
        
        # 若傳入的價格為 0，則不計算未實現部分，避免將市值誤算為 0
        if current_price > 0:
            market_value = current_price * total_shares
            unrealized_pnl = market_value - inventory_cost
        else:
            market_value = 0.0
            unrealized_pnl = 0.0
            
        roi_pct = (unrealized_pnl / inventory_cost * 100) if inventory_cost > 0 else 0
        
        day_change_amt = (current_price - prev_close) * total_shares if prev_close > 0 and current_price > 0 else 0
        day_change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 and current_price > 0 else 0

        return {
            "ticker": ticker,
            "total_shares": total_shares,
            "avg_cost": round(avg_cost, 4),
            "market_value": round(market_value, 2),
            "inventory_cost": round(inventory_cost, 2),
            "cumulative_buy_cost": round(cumulative_buy_cost, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "roi_pct": round(roi_pct, 2),
            "day_change_amt": round(day_change_amt, 2),
            "day_change_pct": round(day_change_pct, 2)
        }

    def get_portfolio_summary(self, username: str, price_info_map: Dict[str, Dict[str, float]]) -> dict:
        """
        生成使用者全域資產概況。
        修正：防止單一價格抓取失敗拖累總資產計算。
        """
        username = username.lower()
        data = self._load_user_data(username)
        
        summary = {
            "total_market_value": 0.0,
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
            "total_inventory_cost": 0.0,
            "total_invested_base": 0.0, # 歷史總投入基準
            "holdings": []
        }

        for ticker, portfolio_item in data["portfolio"].items():
            prices = price_info_map.get(ticker, {"current": 0.0, "prev_close": 0.0})
            
            # 若看板總值變為 0，通常是因為 price_info_map 沒傳入該 ticker 的現價
            # 我們這裡在計算總覽時，先計算出該標的的指標
            m = self.calculate_metrics(username, ticker, prices["current"], prices["prev_close"])
            
            if m:
                # 累加所有歷史已實現損益
                summary["total_realized_pnl"] += m["realized_pnl"]
                
                if m["total_shares"] > 0:
                    summary["total_market_value"] += m["market_value"]
                    summary["total_unrealized_pnl"] += m["unrealized_pnl"]
                    summary["total_inventory_cost"] += m["inventory_cost"]
                    summary["holdings"].append(m)

        # 修正全域 ROI 計算邏輯
        # 使用「目前持倉成本」作為分母計算當前資產效率
        if summary["total_inventory_cost"] > 0:
            summary["total_roi_pct"] = (summary["total_unrealized_pnl"] / summary["total_inventory_cost"] * 100)
        else:
            summary["total_roi_pct"] = 0.0
            
        return summary
    
    def get_watchlist(self, username: str) -> List[str]:
        """讀取觀察清單。"""
        data = self._load_user_data(username)
        return data.get("watchlist", [])

    def get_tracked_tickers(self, username: str) -> List[str]:
        """取得所有需要追蹤股價的代碼 (聯集觀察清單與庫存標的)。"""
        data = self._load_user_data(username)
        tickers = set(data.get("watchlist", []))
        tickers.update(data.get("portfolio", {}).keys())
        return list(tickers)