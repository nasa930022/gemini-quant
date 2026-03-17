"""
PortfolioManager - 負責投資組合交易紀錄管理與財務指標計算。
修正版：採用加權平均成本法 (Weighted Average Cost) 並處理全額損益加總。
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, data_file: Path):
        self.data_file = data_file
        self.data = self._load_data()

    def _load_data(self) -> dict:
        default_structure = {"watchlist": [], "portfolio": {}}
        if not self.data_file.exists():
            return default_structure
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, list):
                return {"watchlist": content, "portfolio": {}}
            return content
        except Exception as e:
            logger.error(f"載入 data.json 失敗: {e}")
            return default_structure

    def save(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_transaction(self, ticker: str, t_type: str, date: str, price: float, shares: float):
        ticker = ticker.upper()
        if ticker not in self.data["watchlist"]:
            self.data["watchlist"].append(ticker)
        if ticker not in self.data["portfolio"]:
            self.data["portfolio"][ticker] = {"transactions": []}
        self.data["portfolio"][ticker]["transactions"].append({
            "date": date, "type": t_type, "price": price, "shares": shares
        })
        self.save()

    def calculate_metrics(self, ticker: str, current_price: float = 0.0, prev_close: float = 0.0) -> dict:
        """
        修正後的財務算法：
        1. 賣出時使用當前平均成本計算損益。
        2. 即使股數為 0，已實現損益也會保留。
        """
        ticker = ticker.upper()
        if ticker not in self.data["portfolio"]:
            return {}

        transactions = self.data["portfolio"][ticker].get("transactions", [])
        total_shares = 0.0
        inventory_cost = 0.0  # 當前库存的總帳面價值 (Book Value)
        realized_pnl = 0.0

        # 排序確保時序正確
        sorted_txs = sorted(transactions, key=lambda x: x['date'])

        for tx in sorted_txs:
            price = tx["price"]
            shares = tx["shares"]
            
            if tx["type"] == "buy":
                total_shares += shares
                inventory_cost += price * shares
            elif tx["type"] == "sell":
                if total_shares > 0:
                    # 賣出時的單位成本 = 當前總庫存成本 / 當前總股數
                    unit_cost = inventory_cost / total_shares
                    # 已實現損益 = (賣價 - 單位成本) * 賣出股數
                    realized_pnl += (price - unit_cost) * shares
                    # 依比例扣除庫存成本
                    total_shares -= shares
                    inventory_cost = total_shares * unit_cost
                else:
                    # 若無庫存賣出，視為異常或空單，此處簡化處理
                    pass

        # 最終計算
        avg_cost = inventory_cost / total_shares if total_shares > 0 else 0
        market_value = current_price * total_shares
        # 未實現損益 = 現值 - 剩餘庫存成本
        unrealized_pnl = market_value - inventory_cost if total_shares > 0 else 0
        roi_pct = (unrealized_pnl / inventory_cost * 100) if inventory_cost > 0 else 0
        
        # 當日變動 (僅對現有持倉有效)
        day_change_amt = (current_price - prev_close) * total_shares if prev_close > 0 else 0
        day_change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0

        return {
            "ticker": ticker,
            "total_shares": total_shares,
            "avg_cost": round(avg_cost, 2),
            "market_value": round(market_value, 2),
            "day_change_amt": round(day_change_amt, 2),
            "day_change_pct": round(day_change_pct, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "roi_pct": round(roi_pct, 2),
            "inventory_cost": round(inventory_cost, 2)
        }

    def get_portfolio_summary(self, price_info_map: Dict[str, Dict[str, float]]) -> dict:
        summary = {
            "total_market_value": 0.0,
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
            "total_inventory_cost": 0.0,
            "holdings": []
        }

        for ticker in self.data["portfolio"]:
            prices = price_info_map.get(ticker, {"current": 0.0, "prev_close": 0.0})
            m = self.calculate_metrics(ticker, prices["current"], prices["prev_close"])
            
            if m:
                # 關鍵修復：無論股數是否為 0，已實現損益都必須納入全域總值
                summary["total_realized_pnl"] += m["realized_pnl"]
                
                # 只有還有持股的才計算市值、成本與未實現
                if m["total_shares"] > 0:
                    summary["total_market_value"] += m["market_value"]
                    summary["total_unrealized_pnl"] += m["unrealized_pnl"]
                    summary["total_inventory_cost"] += m["inventory_cost"]
                    summary["holdings"].append(m)

        # 全域總報酬率 = (未實現 + 已實現) / 剩餘持倉總成本 (或可根據需求調整)
        total_pnl = summary["total_unrealized_pnl"] + summary["total_realized_pnl"]
        summary["total_roi_pct"] = (total_pnl / summary["total_inventory_cost"] * 100) if summary["total_inventory_cost"] > 0 else 0
            
        return summary