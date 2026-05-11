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

    def _generate_with_fallback(self, client: genai.Client, contents: Union[str, list], primary_model: str) -> str:
        """具備多層級 Fallback 與重試機制的生成函數。"""
        # 定義模型優先級順序
        fallbacks = [
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]
        
        # 確保 primary_model 不會在 fallbacks 中重複出現
        models_to_try = [primary_model] + [m for m in fallbacks if m != primary_model]
        
        last_exception = None
        for model in models_to_try:
            try:
                # logger.info(f"嘗試使用模型: {model}")
                response = client.models.generate_content(
                    model=model,
                    contents=contents
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_exception = e
                logger.warning(f"模型 {model} 呼叫失敗: {e}，準備嘗試下一個備援方案。")
                continue
        
        raise RuntimeError(f"所有備援模型皆不可用。最後一個錯誤: {last_exception}")

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
                          api_key: Optional[str] = None,
                          news_data: Optional[dict] = None) -> str:
        """Agent A: 深度技術與基本面綜合分析師。"""
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
            3. 新聞動能與情緒 (自本地代理蒸餾): {json.dumps(news_data, indent=2, ensure_ascii=False) if news_data else "無即時新聞數據"}
            4. 歷史分析脈絡: {hist_ctx}
            5. 技術分析圖表 (視覺支援): 請分析隨附的 K 線圖，辨識形態學特徵。

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
            ### 3. 市場新聞與情緒催化 (News Catalyst)
            整合新聞提煉中的情緒分數與關鍵事件，判斷基本面催化劑對技術面是否有助燃或抑制效果。若無新聞數據請寫「目前無顯著新聞動能」。
            ### 4. 持倉成本與壓力測試 (Position Health)
            基於成本 {portfolio_data.get('avg_cost', 'N/A') if portfolio_data else 'N/A'}，判斷乖離、背離、洗盤或轉空，建議止盈或續抱。
            ### 5. 時序脈絡對比 (Temporal Analysis)
            若有歷史報告，對比走勢是否符合預期或突發反轉。無則略過。
            ### 6. 資產配置再平衡建議 (Portfolio Rebalance)
            依權重 {portfolio_data.get('weight_pct', 0) if portfolio_data else 0}% 與標的屬性，判斷是否需強制減碼或加碼。
            ### 7. 綜合行動建議 (Action Plan)
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
            return self._generate_with_fallback(client, contents, model_name)
        except Exception as e:
            logger.error(f"Deep Analysis 最終失敗: {e}")
            return f"### 分析失敗\n很抱歉，AI 服務目前負載過重或發生錯誤，請稍後再試。\n錯誤訊息: {e}"

    def run_news_augmentation(self,
                              username: str,
                              ticker: str,
                              existing_report: str,
                              news_data: Optional[dict] = None,
                              api_key: Optional[str] = None) -> str:
        """
        Phase 2 增量更新：將新聞情緒整合到既有技術分析報告中。
        相比 run_deep_analysis 的優勢：
        - 不需要重新上傳圖片 (節省 files.upload() 耗時)
        - 不需要傳入完整蒸餾數據與持倉指標 (大幅減少 input tokens)
        - Prompt 更短更聚焦，回應更快
        """
        client = self._get_client(api_key)
        model_name = "gemini-3.1-flash-lite-preview"

        strategy = self.archive.load_strategy(username)
        personality = self._get_personality_prompt(strategy)

        # 從 news_data 中提取結構化摘要，只傳入高相關性內容以節省 token
        if news_data:
            articles = news_data.get("articles", [])
            # 構建精簡的文章摘要 (只傳重點，不傳原始 JSON)
            article_lines = []
            for a in articles:
                rel = a.get("relevance", "?")
                senti = a.get("sentiment", "?")
                cat = a.get("catalyst_type", "?")
                horizon = a.get("impact_horizon", "?")
                article_lines.append(
                    f"- [{cat}/{horizon}] {a.get('key_point', '?')} (相關性:{rel}, 情緒:{senti})"
                )
            articles_text = "\n".join(article_lines) if article_lines else "無有效新聞"

            catalyst = news_data.get("catalyst_breakdown", {})
            catalyst_desc = "、".join(f"{k}({v}篇)" for k, v in catalyst.items()) if catalyst else "無分類"

            news_block = (
                f"來源: {news_data.get('source_type', '?')} | "
                f"有效新聞: {news_data.get('relevant_count', 0)}/{news_data.get('total_fetched', 0)} 篇\n"
                f"加權情緒: {news_data.get('aggregate_sentiment_score', 0.5)} | 催化劑分布: {catalyst_desc}\n\n"
                f"高相關性新聞分析:\n{articles_text}\n\n"
                f"本地代理摘要: {news_data.get('local_llm_summary', '無')}"
            )
        else:
            news_block = "無即時新聞數據"

        prompt = f"""
            角色: 金融新聞整合編輯。標的: {ticker}
            任務: 將最新市場新聞情緒整合到已完成的技術分析報告中，產出最終增補版本。

            {personality}

            【已完成的階段一技術分析報告】
            {existing_report}

            【新聞動能與情緒 (經相關性過濾與多維度分析)】
            {news_block}

            【輸出要求】
            1. 完整保留原報告的所有章節結構與技術分析內容，不可刪減。
            2. 將「### 3. 市場新聞與情緒催化 (News Catalyst)」章節更新為基於真實新聞數據的深度分析，需涵蓋催化劑類型、影響時間軸與情緒方向。
            3. 若新聞情緒與技術趨勢產生矛盾，在「### 7. 綜合行動建議」中追加風險警示或修正建議。
            4. 保持專業自然語言格式，禁用反引號(`)與JSON鍵名，關鍵指標請用**粗體**。
            5. 直接輸出完整的更新版報告 Markdown，不要加任何前言或說明。
            """

        try:
            return self._generate_with_fallback(client, prompt, model_name)
        except Exception as e:
            logger.error(f"News Augmentation 最終失敗: {e}")
            # Fallback: 直接在原報告後附加新聞摘要，確保使用者至少能看到新聞數據
            news_summary = news_data.get('local_llm_summary', '分析不可用') if news_data else '無新聞數據'
            score = news_data.get('aggregate_sentiment_score', 'N/A') if news_data else 'N/A'
            catalyst_info = ""
            if news_data and news_data.get("catalyst_breakdown"):
                catalyst_info = f"\n**催化劑分布**: {news_data['catalyst_breakdown']}"
            return existing_report + (
                f"\n\n---\n### 📰 市場新聞補充 (自動附加)\n"
                f"**加權情緒分數**: {score}{catalyst_info}\n\n{news_summary}\n"
            )

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
            text = self._generate_with_fallback(client, prompt, model_name)
        except Exception as e:
            logger.error(f"Decision Summary 最終失敗: {e}")
            return {
                "trend": "未知",
                "recommendation": "觀望",
                "entry_price": 0,
                "exit_price": 0,
                "stop_loss": 0,
                "confidence_score": 0,
                "personalized_note": f"系統忙碌中，決策失敗: {e}"
            }

        match = re.search(r'---DECISION_SUMMARY---(.*?)---END_SUMMARY---', text, re.DOTALL)
        if match:
            json_str = match.group(1).strip().replace("```json", "").replace("```", "")
            return json.loads(json_str)
        raise ValueError("無法解析決策 JSON。")

    def parse_portfolio_image(self, image_bytes: bytes, mime_type: str, api_key: Optional[str] = None) -> list[dict]:
        """使用 Gemini 視覺模型解析券商持股截圖。"""
        client = self._get_client(api_key)
        model_name = "gemini-2.5-flash"  # 使用較穩定的 2.5-flash 處理圖像與結構化資料

        prompt = """
        任務：解析這張券商或記帳軟體的投資組合截圖，擷取所有持股或交易資訊。
        請忽略現金餘額或非股票/ETF的項目。
        
        輸出要求：
        請回傳一個嚴格的 JSON 陣列 (Array)，陣列中包含多個物件，每個物件代表一筆紀錄。
        每個物件必須包含以下五個鍵 (Key)：
        - "ticker" (字串)：股票代碼 (必須是大寫英文字母，如 AAPL, TSLA)
        - "price" (數字)：成交單價或平均成本 (請過濾掉幣別符號與逗號)
        - "shares" (數字)：成交股數或持股數量 (請過濾掉逗號)
        - "date" (字串或 null)：交易日期 (格式 YYYY-MM-DD)。如果圖中只有總量和均價，沒有明確交易日期，請設為 null。
        - "type" (字串或 null)：交易類型 ("buy" 或 "sell")。如果圖中只是庫存總覽沒有明確買賣動作，請設為 null。
        
        如果找不到任何股票資訊，請回傳空陣列 []。
        請只回傳 JSON 字串，不要加上 ```json 或任何多餘的說明。
        """
        
        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt
        ]
        
        try:
            text = self._generate_with_fallback(client, contents, model_name)
            # 簡單清理可能存在的 markdown 包裝
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            
            result = json.loads(cleaned.strip())
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Image parsing failed: {e}")
            raise ValueError(f"無法解析圖片：{str(e)}")

    def parse_portfolio_text(self, text_data: str, api_key: Optional[str] = None) -> list[dict]:
        """使用 Gemini 解析非結構化或雜亂的文字投資組合資料。"""
        client = self._get_client(api_key)
        model_name = "gemini-2.5-flash-lite"
        
        prompt = f"""
        任務：從以下使用者貼上的文字中，擷取所有投資組合或詳細交易紀錄資訊。
        
        輸入文字：
        {text_data}
        
        輸出要求：
        請回傳一個嚴格的 JSON 陣列 (Array)，每個物件代表一筆紀錄。
        每個物件必須包含以下五個鍵：
        - "ticker" (字串)：股票代碼 (必須是大寫英文字母)
        - "price" (數字)：成交單價或平均成本
        - "shares" (數字)：成交股數或持股總數
        - "date" (字串或 null)：交易日期 (格式 YYYY-MM-DD)。如果文字中只有總量和均價，請設為 null。
        - "type" (字串或 null)：交易類型 ("buy" 或 "sell")。如果文字中只是庫存總覽，請設為 null。
        
        如果資料不足或無法判斷，請盡可能推測，若完全找不到股票則回傳 []。
        請只回傳 JSON 字串，不要加上 ```json 等標籤。
        """
        
        try:
            text = self._generate_with_fallback(client, prompt, model_name)
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
                
            result = json.loads(cleaned.strip())
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Text parsing failed: {e}")
            raise ValueError(f"無法解析文字：{str(e)}")