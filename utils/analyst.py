"""
Analyst - 整合技術面與資產配置權重的雙代理人 AI 分析核心。
更新：支援從前端動態注入 API Key。
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import google.generativeai as genai
from .archive import ArchiveManager

logger = logging.getLogger(__name__)

# 定義廣泛型、已分散風險的指數型 ETF 清單
DIVERSIFIED_ETFS = {"VOO", "VT", "VXUS", "SPY", "IVV", "VTI", "QQQ"}

class Analyst:
    def __init__(self, archive: Optional[ArchiveManager] = None) -> None:
        self.archive = archive or ArchiveManager()
        self._model = None
        # 初始化時不強制載入 Key，改為在執行時動態載入

    def _setup_model(self, api_key: Optional[str] = None) -> None:
        """根據傳入的 Key 或環境變數初始化模型。"""
        target_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not target_key:
            raise RuntimeError("未提供 API Key，請在網頁輸入或設定環境變數。")
        
        try:
            genai.configure(api_key=target_key)
            # 建議使用 gemini-2.0-flash 獲得更穩定的付費配額與效能
            self._model = genai.GenerativeModel("gemini-2.5-flash") 
            logger.info("Gemini Model 已動態初始化")
        except Exception as e:
            logger.error("初始化 Gemini 模型失敗: %s", e)
            raise

    def _get_historical_context(self, ticker: str, as_of: datetime) -> str:
        """搜尋最近 3 個交易日的分析報告。"""
        root = self.archive.root / ticker.upper()
        if not root.exists(): return ""
        candidates = []
        for child in root.iterdir():
            if not child.is_dir(): continue
            try:
                d = datetime.strptime(child.name, "%Y-%m-%d")
                if d.date() < as_of.date():
                    report_path = child / "analysis_report.md"
                    if report_path.exists(): candidates.append((d, report_path))
            except ValueError: continue
        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[:3]
        if not selected: return "（無歷史報告）"
        parts = [f"### 歷史報告日期: {d.strftime('%Y-%m-%d')}\n{p.read_text(encoding='utf-8')}\n---" for d, p in selected]
        return "\n".join(parts)

    def run_deep_analysis(self, 
                          ticker: str, 
                          distilled_json: dict, 
                          historical_context: str, 
                          portfolio_data: Optional[dict] = None,
                          image_path: Optional[Path] = None,
                          api_key: Optional[str] = None) -> str:
        """Agent A: 深度技術分析與資產配置診斷。"""
        
        # 關鍵修正：執行前先動態設定 Key
        self._setup_model(api_key)

        ticker = ticker.upper()
        weight = portfolio_data.get('weight_pct', 0) if portfolio_data else 0
        is_diversified = ticker in DIVERSIFIED_ETFS
        
        asset_type_desc = "【廣泛指數型 ETF】(核心配置，無集中度限制)" if is_diversified else "【個股/主動型資產】(衛星配置，需監控集中度風險)"

        portfolio_ctx_str = "使用者目前未持有的觀察標的。"
        if portfolio_data and portfolio_data.get("total_shares", 0) > 0:
            portfolio_ctx_str = f"""
            使用者目前持股狀況：
            - 標的屬性：{asset_type_desc}
            - 平均成本: {portfolio_data.get('avg_cost')}
            - 目前損益百分比: {portfolio_data.get('roi_pct')}%
            - 該股票佔總投資組合比例: {weight}%
            """

        prompt = f"""
            角色：高級金融分析師與資產配置專家
            任務：針對 {ticker} 進行技術面與『資產配置健康度』深度診斷。
            
            輸入資料：
            1. 蒸餾技術指標 (JSON)
            2. 歷史分析脈絡
            3. 個人持股與比例：{portfolio_ctx_str}
            4. 技術分析 K 線圖 (視覺支援)

            資產配置診斷規則：
            - 如果是『廣泛指數型 ETF』：即便權重極高也視為健康配置。除非技術面出現系統性崩盤風險，否則不建議因權重原因減碼。
            - 如果是『個股/主動型資產』：權重超過 25-30% 視為過度集中。應結合技術面壓力位給出減碼至其他標的的建議。權重低於 5% 則建議分批建立基本倉位。


            輸出結構 (Markdown)：
            ### 1. 趨勢定位 (Trend Alignment)
            分析目前股價與 MA200/MA50 的相對位置。
            ### 2. 關鍵指標解讀 (Indicators)
            解讀 RSI、布林通道與成交量的變化。
            ### 3. 持股成本與壓力診斷 (Position Diagnostics)
            結合使用者的「平均成本」進行分析。
            - 若目前獲利：分析是否出現過熱訊號，應繼續持有或部分入袋為安？
            - 若目前虧損：結合技術面支撐，判斷是「洗盤回測」還是「趨勢轉空」？
            ### 4. 時序脈絡對比 (Temporal Analysis)
            對比前三日報告，趨勢是延續還是反轉？
            ### 5. 資產配置再平衡建議 (考慮標的屬性)
            ### 6. 綜合行動建議
            給出針對該標的的具體操作計畫。

            數據 JSON:
            {json.dumps(distilled_json, indent=2, ensure_ascii=False)}
            """
        
        parts = [prompt]
        if image_path and image_path.exists():
            img = genai.upload_file(path=str(image_path))
            parts.append(img)

        response = self._model.generate_content(parts)
        return response.text

    def run_decision_summary(self, 
                             report_md: str, 
                             distilled_json: dict, 
                             portfolio_data: Optional[dict] = None,
                             image_path: Optional[Path] = None,
                             api_key: Optional[str] = None) -> dict:
        """Agent B: 執行決策官。"""
        
        # 關鍵修正：執行前先動態設定 Key
        self._setup_model(api_key)
        
        ticker = portfolio_data.get('ticker', '').upper() if portfolio_data else ""
        weight = portfolio_data.get('weight_pct', 0) if portfolio_data else 0
        is_diversified = ticker in DIVERSIFIED_ETFS

        prompt = f"""
            角色：首席執行官 (CEO)
            任務：根據分析官報告與分類資產配置規則給出最終交易指令。
            
            強制的再平衡準則：
            1. 分類風險控制：
               - 對於『個股』：若權重 > 30%，即使看漲，建議指令也應考慮「減碼/持有」而非「加碼」。
               - 對於『廣泛型 ETF (VOO/VT/VXUS)』：豁免集中度壓制。可根據技術面給出「加碼/持續持有」指令。
            2. 弱勢汰換：若個股虧損 > 15% 且權重過高，優先建議「賣出轉入廣泛型 ETF」。

            輸出格式 (嚴格 JSON)：
            ---DECISION_SUMMARY---
            {{
            "trend": "上漲/盤整/下跌",
            "recommendation": "加碼/購入/持有/減碼/賣出/觀望",
            "rebalance_reason": "根據標的屬性說明的配置邏輯，或 N/A",
            "entry_price": 數字或 "N/A",
            "exit_price": 數字或 "N/A",
            "stop_loss": 數字或 "N/A",
            "confidence_score": 0-100
            }}
            ---END_SUMMARY---

            分析官報告：
            {report_md}
            """
        
        parts = [prompt]
        if image_path and image_path.exists():
            parts.append("(參考前述圖表)")

        response = self._model.generate_content(parts)
        text = response.text

        match = re.search(r'---DECISION_SUMMARY---(.*?)---END_SUMMARY---', text, re.DOTALL)
        if match:
            json_str = match.group(1).strip().replace("```json", "").replace("```", "")
            return json.loads(json_str)
        raise ValueError(f"無法解析決策 JSON。原始輸出: {text}")