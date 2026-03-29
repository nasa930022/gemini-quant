"""
analyst.py - 靈魂模組：具備「性格與策略意識」的雙代理人 AI
功能：
1. 動態 Prompt 注入：根據使用者 strategy.json 給予不同分析權重。
2. 雙代理人模型分流：
   - Analyst (分析官): 使用 gemini-3.1-flash-lite-preview，負責技術與視覺診斷。
   - Risk Executive (決策官): 使用 gemini-3.1-flash-lite-preview，負責嚴格格式化決策。
3. 自然語言優化：移除程式碼風格鍵名，提升報告可讀性。
Phase 3 遷移：從 google.generativeai 改為 google.genai SDK。
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, Dict, Any

from google import genai
from google.genai import types
from .archive import ArchiveManager

logger = logging.getLogger(__name__)

# 定義廣泛型、已分散風險的指數型 ETF 清單 (用於風險豁免邏輯)
DIVERSIFIED_ETFS = {"VOO", "VT", "VXUS", "SPY", "IVV", "VTI", "QQQ"}

class Analyst:
    def __init__(self, archive: Optional[ArchiveManager] = None) -> None:
        self.archive = archive or ArchiveManager()

    def _get_client(self, api_key: Optional[str] = None) -> genai.Client:
        """建立 google.genai Client 實例。"""
        target_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not target_key:
            raise RuntimeError("未提供 API Key，請在網頁輸入或於 .env 設定 GEMINI_API_KEY。")
        return genai.Client(api_key=target_key)

    def _get_historical_context(self, username: str, ticker: str, as_of: datetime) -> str:
        """搜尋該使用者最近 3 個交易日的分析報告。"""
        root = self.archive.get_report_path(username, ticker, as_of).parent
        if not root.exists():
            return "（無歷史報告）"

        candidates = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                d = datetime.strptime(child.name, "%Y-%m-%d")
                if d.date() < as_of.date():
                    report_path = child / "analysis_report.md"
                    if report_path.exists():
                        candidates.append((d, report_path))
            except ValueError:
                continue

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[:3]
        if not selected:
            return "（無歷史報告）"

        parts = [
            f"### 歷史報告日期: {d.strftime('%Y-%m-%d')}\n{p.read_text(encoding='utf-8')}\n---"
            for d, p in selected
        ]
        return "\n".join(parts)

    def _get_personality_prompt(self, strategy: Dict) -> str:
        """根據使用者策略，生成專屬的分析風格指引。"""
        style = strategy.get("trading_style", "一般")
        risk = strategy.get("risk_tolerance", "一般")
        freq = strategy.get("trading_frequency", "長期")

        prompt = f"你的分析風格必須嚴格符合使用者的投資偏好：【風格：{style} / 風險：{risk} / 頻率：{freq}】\n"

        if style == "激進":
            prompt += "- 側重「動能突破」與「趨勢追蹤」，在多頭趨勢中放寬對超買的容忍度。\n"
        elif style == "保守":
            prompt += "- 側重「價值位階」與「支撐強度」，強調資金安全性與分批進場。\n"
        else:
            prompt += "- 採取「平衡策略」，結合 50% 穩定度與 50% 動能指標。\n"

        if risk == "低":
            prompt += "- 嚴格執行「停損優先」邏輯，當股價跌破 MA20 或回撤超過 10% 時需發出強烈警訊。\n"
        elif risk == "高":
            prompt += "- 容忍較大的波動回撤（20-30%），專注於高 Beta 標的的向上爆發力。\n"

        return prompt

    def run_deep_analysis(self,
                          username: str,
                          ticker: str,
                          distilled_json: dict,
                          portfolio_data: Optional[dict] = None,
                          image_path: Optional[Path] = None,
                          api_key: Optional[str] = None) -> str:
        """Agent A: 深度技術分析師。"""
        client = self._get_client(api_key)
        model_name = "gemini-3.1-flash-lite-preview"
        # model_name = "gemini-3-flash-preview"

        strategy = self.archive.load_strategy(username)
        hist_ctx = self._get_historical_context(username, ticker, datetime.now())
        personality = self._get_personality_prompt(strategy)

        ticker = ticker.upper()
        is_etf = ticker in DIVERSIFIED_ETFS
        asset_type_desc = (
            "【廣泛指數型 ETF】(核心資產，豁免集中度限制)" if is_etf
            else "【個股/主動型資產】(衛星配置，需監控集中度風險)"
        )

        prompt = f"""
            角色:高級金融分析師。任務:為 {ticker} 產出技術與資產配置深度診斷。

            【1. 使用者投資性格與約束】
            - 交易風格：{strategy['trading_style']}
            - 風險承受度：{strategy['risk_tolerance']}
            - 投資頻率：{strategy['trading_frequency']}

            【2. 輸入數據】
            1. 個人化蒸餾技術指標 (JSON): {json.dumps(distilled_json, indent=2, ensure_ascii=False)}
            2. 持股現況: {json.dumps(portfolio_data, indent=2, ensure_ascii=False) if portfolio_data else "目前未持倉"}
            3. 歷史分析脈絡: {hist_ctx}
            4. 技術分析圖表 (視覺支援): 請分析隨附的 K 線圖，辨識形態學特徵。

            【3. 資產配置與風險規則】
            - 標的屬性：{asset_type_desc}
            - 集中度規則：個股權重 > 20% 視為過度集中；指數型ETF 無上限。
            - 停損規則：根據風險承受度給出精確停損點位。

            ---
            【輸出要求】
            1. 使用專業自然語言，絕對禁用反引號(`)與JSON鍵名，關鍵指標請用**粗體**。
            2. 嚴格按以下Markdown結構與提示輸出：

            ### 1. 策略符合度診斷 (Strategy Alignment)
            評估波段特質是否符合其「{strategy['trading_style']}」風格(如震盪或動能)。
            ### 2. 核心趨勢與指標解讀 (Trend & Indicators)
            分析均線位階、RSI、布林通道與量價，並結合視覺圖表說明當前形態(如築底、高檔盤整)。
            ### 3. 持倉成本與壓力測試 (Position Health)
            基於成本 {portfolio_data.get('avg_cost', 'N/A') if portfolio_data else 'N/A'}，判斷乖離、背離、洗盤或轉空，建議止盈或續抱。
            ### 4. 時序脈絡對比 (Temporal Analysis)
            若有歷史報告，對比走勢是否符合預期或突發反轉。無則略過。
            ### 5. 資產配置再平衡建議 (Portfolio Rebalance)
            依權重 {portfolio_data.get('weight_pct', 0) if portfolio_data else 0}% 與標的屬性，判斷是否需強制減碼或加碼。
            ### 6. 綜合行動建議 (Action Plan)
            提供具體且可執行的價格點位(買入/減碼/觀望)與下一步行動。
            """

        contents: list = [prompt]

        # 上傳圖片 (新版 SDK 使用 client.files.upload)
        if image_path and Path(image_path).exists():
            uploaded = client.files.upload(
                file=str(image_path),
                config=types.UploadFileConfig(mime_type="image/png")
            )
            contents.append(uploaded)

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents
            )
            return response.text
        except Exception as e:
            fallback_model = "gemini-2.5-flash"
            logger.warning(f"使用 {model_name} 失敗: {e}，自動切換至 {fallback_model}")
            response = client.models.generate_content(
                model=fallback_model,
                contents=contents
            )
            return response.text

    def run_decision_summary(self,
                             username: str,
                             report_md: str,
                             portfolio_data: Optional[dict] = None,
                             api_key: Optional[str] = None) -> dict:
        """Agent B: 執行決策官。"""
        client = self._get_client(api_key)
        model_name = "gemini-3.1-flash-lite-preview"
        # model_name = "gemini-3-flash-preview"

        strategy = self.archive.load_strategy(username)
        risk_level = strategy.get("risk_tolerance", "一般")

        stop_loss_limit = {
            "低": "嚴格，個股虧損 > 8% 強制執行賣出建議",
            "一般": "中等，個股虧損 > 15% 執行防禦性調整",
            "高": "寬鬆，專注趨勢是否反轉"
        }.get(risk_level, "一般限制")

        prompt = f"""
            角色：執行決策官 (CEO)
            任務：將分析報告轉化為可執行的 JSON 指令。
            
            【強制風險限制】
            - 使用者風險承受度：{risk_level}
            - 停損原則：{stop_loss_limit}
            - 權重限制：個股權重 > 20% 且風險度為「低/一般」時，視為過度集中，強制建議減碼，廣泛指數型 ETF 不在此限。

            輸出格式 (嚴格 JSON)：
            ---DECISION_SUMMARY---
            {{
            "trend": "上漲/盤整/下跌",
            "recommendation": "加碼/購入/持有/減碼/賣出/觀望",
            "entry_price": 數字,
            "exit_price": 數字,
            "stop_loss": 數字,
            "confidence_score": 0-100,
            "personalized_note": "一句話說明為何符合該使用者的風格"
            }}
            ---END_SUMMARY---

            分析官報告內容：
            {report_md}
            """

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
        except Exception as e:
            fallback_model = "gemini-2.5-flash"
            logger.warning(f"使用 {model_name} 失敗: {e}，自動切換至 {fallback_model}")
            response = client.models.generate_content(
                model=fallback_model,
                contents=prompt
            )
        text = response.text

        match = re.search(r'---DECISION_SUMMARY---(.*?)---END_SUMMARY---', text, re.DOTALL)
        if match:
            json_str = match.group(1).strip().replace("```json", "").replace("```", "")
            return json.loads(json_str)
        raise ValueError("無法解析決策 JSON。")