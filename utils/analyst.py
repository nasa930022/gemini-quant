"""
analyst.py - 靈魂模組：具備「性格與策略意識」的雙代理人 AI
功能：
1. 動態 Prompt 注入：根據使用者 strategy.json 給予不同分析權重。
2. 雙代理人工作流：
   - Analyst (分析官)：負責技術面解讀與客製化策略產出。
   - Risk Executive (風險執行官)：負責最終決策，並強制執行風險管控。
3. 歷史脈絡繼承：自動檢索該使用者的過去報告，確保建議的連貫性。
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, Dict, Any

import google.generativeai as genai
from .archive import ArchiveManager

logger = logging.getLogger(__name__)

# 定義廣泛型、已分散風險的指數型 ETF 清單 (用於風險豁免邏輯)
DIVERSIFIED_ETFS = {"VOO", "VT", "VXUS", "SPY", "IVV", "VTI", "QQQ"}

class Analyst:
    def __init__(self, archive: Optional[ArchiveManager] = None) -> None:
        self.archive = archive or ArchiveManager()
        self._model = None

    def _setup_model(self, api_key: Optional[str] = None) -> None:
        """根據傳入的 Key 或環境變數初始化模型。"""
        target_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not target_key:
            raise RuntimeError("未提供 API Key，請在網頁輸入或設定環境變數。")
        
        try:
            genai.configure(api_key=target_key)
            # 使用 gemini-3-flash 提供高效能多模態分析 (不要改)
            self._model = genai.GenerativeModel("gemini-3-flash-preview") 
            logger.info("Gemini Model 已動態初始化")
        except Exception as e:
            logger.error("初始化 Gemini 模型失敗: %s", e)
            raise

    def _get_historical_context(self, username: str, ticker: str, as_of: datetime) -> str:
        """搜尋該使用者最近 3 個交易日的分析報告。"""
        root = self.archive.get_report_path(username, ticker, as_of).parent
        if not root.exists(): return "（無歷史報告）"
        
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

    def _get_personality_prompt(self, strategy: Dict) -> str:
        """根據使用者策略，生成專屬的分析風格指引。"""
        style = strategy.get("trading_style", "一般")
        risk = strategy.get("risk_tolerance", "一般")
        freq = strategy.get("trading_frequency", "長期")

        # 基礎人格設定
        prompt = f"你的分析風格必須嚴格符合使用者的投資偏好：【風格：{style} / 風險：{risk} / 頻率：{freq}】\n"

        # 風格細節
        if style == "激進":
            prompt += "- 側重「動能突破」與「趨勢追蹤」，在多頭趨勢中放寬對超買的容忍度。\n"
        elif style == "保守":
            prompt += "- 側重「價值位階」與「支撐強度」，強調資金安全性與分批進場。\n"
        else:
            prompt += "- 採取「平衡策略」，結合 50% 穩定度與 50% 動能指標。\n"

        # 風險細節
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
        """Agent A: 深度技術分析師 (根據使用者性格調整)。"""
        
        self._setup_model(api_key)
        strategy = self.archive.load_strategy(username)
        hist_ctx = self._get_historical_context(username, ticker, datetime.now())
        
        personality = self._get_personality_prompt(strategy)
        
        ticker = ticker.upper()
        weight = portfolio_data.get('weight_pct', 0) if portfolio_data else 0
        is_etf = ticker in DIVERSIFIED_ETFS

        # 定義標的屬性描述
        asset_type_desc = "【廣泛指數型 ETF】(核心資產，豁免集中度限制)" if is_etf else "【個股/主動型資產】(衛星配置，需監控集中度風險)"

        prompt = f"""
            角色：高級金融分析師、資產配置專家與客製化投資顧問
            任務：針對 {ticker} 進行技術面趨勢與『個人資產配置健康度』的深度診斷。

            【1. 使用者投資性格與約束】
            {personality}
            - 交易風格：{strategy['trading_style']} (影響對技術指標的敏感度與操作激進程度)
            - 風險承受度：{strategy['risk_tolerance']} (決定停損容忍度與部位集中度限制)
            - 投資頻率：{strategy['trading_frequency']} (決定分析的時間尺度與持倉週期建議)

            【2. 輸入數據】
            1. 個人化蒸餾技術指標 (JSON): 
            {json.dumps(distilled_json, indent=2, ensure_ascii=False)}
            2. 持股與權重現況: 
            {json.dumps(portfolio_data, indent=2, ensure_ascii=False) if portfolio_data else "目前未持倉，僅作觀察。"}
            3. 歷史分析脈絡 (前三日): 
            {hist_ctx}
            4. 技術分析圖表 (視覺支援): 請分析隨附的 K 線圖，辨識形態學特徵 (如支撐壓力、量價關係)。

            【3. 資產配置與風險規則】
            - 標的屬性：{asset_type_desc}
            - 集中度規則：
                * 若為『個股』：權重 > 25-30% 定義為過度集中。即便技術面看漲，也必須給出「減碼至核心資產」的避險建議。
                * 若為『廣泛型 ETF』：無權重上限。除非出現系統性空頭，否則不建議因權重原因減碼。
            - 停損規則：
                * 根據風險承受度：低風險者需嚴守 8-10% 停損；高風險者可容忍 20% 波動或以趨勢反轉 (如破 MA50) 為準。

            ---
            請根據以上規範，產生結構化的 Markdown 報告：

            ### 1. 策略符合度診斷 (Strategy Alignment)
            - 評估 {ticker} 目前的波段特質是否符合使用者的「{strategy['trading_style']}」風格。
            - 若使用者為「保守型」，當前標的是否過於震盪？若為「激進型」，目前是否有足夠的動能突破？

            ### 2. 核心趨勢與指標解讀 (Trend & Indicators)
            - 分析價格與 MA10/20/50/200 的位階關係。
            - 解讀 RSI 是否背離、布林通道是否開口、以及量價配合情況。
            - 結合「視覺圖表」描述目前處於什麼樣的形態 (例如：底型築起、末跌段、或高檔盤整)。

            ### 3. 持倉成本與壓力測試 (Position Health)
            - 結合使用者的「平均成本 ({portfolio_data.get('avg_cost', 'N/A')})」分析。
            - **若目前獲利**：判斷是否出現「乖離過大」或「量價背離」，應續抱還是執行「移動止盈」？
            - **若目前虧損**：判斷是正常的「洗盤回測」還是趨勢已「轉空確認」？

            ### 4. 時序脈絡對比 (Temporal Analysis) (若沒有歷史報告則略過此段落)
            - 對比歷史報告，目前的走勢是「符合預期地延續」還是「突發性的反轉」？
            - 過去兩天擔憂的風險是否已消除？

            ### 5. 資產配置再平衡建議 (Portfolio Rebalance)
            - 根據目前的權重 ({portfolio_data.get('weight_pct', 0)}%) 與標的屬性給出具體建議。
            - 是否因「個股過度集中」需要強制減碼？或是因為「核心資產位階極低」建議加碼？

            ### 6. 綜合行動建議 (Action Plan)
            - 給出具體、可執行的下一步建議（例如：分批於 $X 價格買入、跌破 $Y 減碼、或觀望至 $Z 訊號出現）。
            """
        
        parts = [prompt]
        if image_path and image_path.exists():
            img = genai.upload_file(path=str(image_path))
            parts.append(img)

        response = self._model.generate_content(parts)
        return response.text

    def run_decision_summary(self, 
                             username: str,
                             report_md: str, 
                             portfolio_data: Optional[dict] = None,
                             api_key: Optional[str] = None) -> dict:
        """Agent B: 執行決策官 (負責最終買賣指令與風險執行)。"""
        
        self._setup_model(api_key)
        strategy = self.archive.load_strategy(username)
        risk_level = strategy.get("risk_tolerance", "一般")
        
        # 不同的風險等級對應不同的停損限制
        stop_loss_limit = {
            "低": "嚴格，個股虧損 > 8% 強制執行賣出建議",
            "一般": "中等，個股虧損 > 15% 執行防禦性調整",
            "高": "寬鬆，專注趨勢是否反轉，而非單純百分比"
        }.get(risk_level, "一般限制")

        prompt = f"""
            角色：執行決策官 (CEO)
            任務：將分析報告轉化為可執行的 JSON 指令。
            
            【強制執行的風險限制】
            - 使用者風險承受度：{risk_level}
            - 停損原則：{stop_loss_limit}
            - 權重限制：個股權重 > 25% 且風險度為「低/一般」時，禁止建議「加碼」。

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
        
        response = self._model.generate_content(prompt)
        text = response.text

        match = re.search(r'---DECISION_SUMMARY---(.*?)---END_SUMMARY---', text, re.DOTALL)
        if match:
            json_str = match.group(1).strip().replace("```json", "").replace("```", "")
            return json.loads(json_str)
        raise ValueError(f"無法解析決策 JSON。")