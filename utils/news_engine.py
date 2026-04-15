"""
news_engine.py - Edge-Cloud Hybrid 新聞分析抓取與本地推理引擎 (v2)
增強功能：
1. 相關性過濾：對每篇文章評分，過濾與標的無關的雜訊新聞。
2. 多維度分析：除情緒外，分析催化劑類型（財報/監管/總經/產品等）與影響時間軸。
3. 加權情緒：依相關性加權計算整體情緒，避免無關新聞稀釋訊號。
4. 抓取 yfinance 新聞，若不足3篇則抓取 SPY。
5. 平行拋給 Edge Local LLM (目前以 Gemini 模擬)。
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union
import yfinance as yf
from google import genai

from .archive import ArchiveManager

logger = logging.getLogger(__name__)

# 相關性門檻：低於此分數的文章視為雜訊，排除出有效分析
_RELEVANCE_THRESHOLD = 0.4


class LocalInferenceClient:
    """
    負責與本地 vLLM 或相容 OpenAI 格式的推理伺服器溝通。
    此版本為了測試方便，暫時封裝為使用 Google Gemini SDK 作為「在地代理」平替。
    待 vLLM 伺服器備妥後可輕易抽換此處的 provider 或 client API。
    """
    def __init__(self, api_key: Optional[str] = None):
        import os
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("LocalInferenceClient 缺少 API Key，新聞情緒蒸餾將失敗。")
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        # 模擬本地模型，實際應用中配置 VLLM_MODEL_NAME，例如 "meta-llama/Llama-4-8B-Instruct"
        self.model_name = "gemini-2.5-flash-lite"

    async def _generate_with_fallback_async(self, contents: Union[str, list]) -> str:
        """非同步版本的生成函數，具備備援機制。"""
        fallbacks = [
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-2.0-flash"
        ]
        models_to_try = [self.model_name] + [m for m in fallbacks if m != self.model_name]

        last_exception = None
        for model in models_to_try:
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=contents
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_exception = e
                logger.warning(f"[News] 模型 {model} 呼叫失敗: {e}，切換備援。")
                continue
        raise RuntimeError(f"新聞分析備援模型皆不可用: {last_exception}")

    async def _async_analyze_article(self, ticker: str, article: Dict) -> Dict:
        """
        非同步多維度分析單篇文章。
        相比舊版新增：relevance (相關性)、catalyst_type (催化劑類型)、impact_horizon (影響時間軸)。
        Prompt 僅增加約 30 tokens，但資訊密度顯著提升。
        """
        content_obj = article.get("content", article)
        title = content_obj.get("title", "無標題")
        summary_text = content_obj.get("summary", "")

        if not self.client:
            return {"title": title, "relevance": 0.0, "sentiment": 0.5,
                    "catalyst_type": "unknown", "impact_horizon": "unknown",
                    "key_point": "API Key缺失"}

        prompt = f"""角色: 金融快訊分析員。分析標的: {ticker}
任務: 判斷以下新聞與 {ticker} 的相關性，並提取多維度分析。

新聞標題: {title}
新聞摘要: {summary_text if summary_text else "無摘要"}

**輸出要求 (僅輸出嚴格 JSON，不可有其他文字)**
{{
    "relevance": 0.0到1.0 (與{ticker}的直接相關度, 0=完全無關, 0.5=間接相關, 1.0=直接提及{ticker}),
    "sentiment": 0.0到1.0 (對{ticker}的影響方向, 0=極端利空, 0.5=中立, 1.0=極端利多),
    "catalyst_type": "earnings|regulation|macro|competition|product|sector|management|partnership|other" 中選一,
    "impact_horizon": "short|mid|long" 中選一 (short=1週內, mid=1-3月, long=3月+),
    "key_point": "用20字內中文摘要對{ticker}的核心影響"
}}"""
        try:
            text_response = await self._generate_with_fallback_async(prompt)
            # 清理 LLM 可能產生的 markdown 包裹
            cleaned = text_response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            data_dict = json.loads(cleaned.strip())
            return {
                "title": title,
                "relevance": round(float(data_dict.get("relevance", 0.5)), 2),
                "sentiment": round(float(data_dict.get("sentiment", 0.5)), 2),
                "catalyst_type": data_dict.get("catalyst_type", "other"),
                "impact_horizon": data_dict.get("impact_horizon", "mid"),
                "key_point": data_dict.get("key_point", "分析失敗")
            }
        except Exception as e:
            logger.error(f"分析文章失敗: {e}")
            return {"title": title, "relevance": 0.0, "sentiment": 0.5,
                    "catalyst_type": "other", "impact_horizon": "mid",
                    "key_point": "分析發生錯誤"}

    async def distill_news_batch(self, ticker: str, source_type: str, articles: List[Dict]) -> Dict:
        """接收多篇文章並行蒸餾，過濾低相關性雜訊，最後產生總結。"""
        if not articles:
            return self._build_empty_result(ticker, source_type)

        # 1. 平行處理所有文章的多維度分析
        tasks = [self._async_analyze_article(ticker, a) for a in articles]
        all_articles = await asyncio.gather(*tasks)

        # 2. 依相關性分流：高相關 vs 過濾掉的雜訊
        relevant = [a for a in all_articles if a.get("relevance", 0) >= _RELEVANCE_THRESHOLD]
        filtered_out = [a for a in all_articles if a.get("relevance", 0) < _RELEVANCE_THRESHOLD]

        if not relevant:
            # 所有新聞都與標的無關，降級使用全部但標記 source_type
            relevant = all_articles
            source_type = f"{source_type}_LowRelevance"

        # 3. 加權情緒計算：以相關性作為權重，避免無關新聞稀釋訊號
        total_weight = sum(a.get("relevance", 0.5) for a in relevant)
        if total_weight > 0:
            weighted_sentiment = sum(
                a.get("sentiment", 0.5) * a.get("relevance", 0.5) for a in relevant
            ) / total_weight
        else:
            weighted_sentiment = 0.5

        # 4. 催化劑分布統計
        catalyst_counts: Dict[str, int] = {}
        for a in relevant:
            ct = a.get("catalyst_type", "other")
            catalyst_counts[ct] = catalyst_counts.get(ct, 0) + 1

        # 5. 生成統合摘要 (基於高相關性文章)
        summary = await self._generate_aggregate_summary(ticker, relevant, weighted_sentiment, catalyst_counts)

        return {
            "ticker": ticker.upper(),
            "timestamp": datetime.now().isoformat(),
            "source_type": source_type,
            "total_fetched": len(all_articles),
            "relevant_count": len(relevant),
            "articles": relevant,
            "filtered_articles": filtered_out,
            "catalyst_breakdown": catalyst_counts,
            "local_llm_summary": summary,
            "aggregate_sentiment_score": round(weighted_sentiment, 2)
        }

    async def _generate_aggregate_summary(self, ticker: str, relevant_articles: List[Dict],
                                           weighted_score: float, catalyst_counts: Dict[str, int]) -> str:
        """基於高相關性文章與催化劑分布，生成結構化綜合摘要。"""
        if not self.client:
            return "無法生成摘要 (未登入或無 API Key)"

        # 構建文章重點列表 (含催化劑標籤)
        points = [
            f"- [{a.get('catalyst_type', '?')}] {a['key_point']} (相關性:{a.get('relevance', '?')}, 情緒:{a.get('sentiment', '?')})"
            for a in relevant_articles
        ]
        points_text = "\n".join(points)

        # 催化劑分布描述
        catalyst_desc = "、".join(f"{k}({v}篇)" for k, v in catalyst_counts.items()) if catalyst_counts else "無分類"

        prompt = f"""角色: 金融快訊主編。標的: {ticker}
以下是經過相關性過濾的 {len(relevant_articles)} 篇重點新聞分析：
{points_text}

催化劑分布: {catalyst_desc}
加權情緒分數: {weighted_score:.2f} (0=看空, 1=看多)

請用80字以內的中文產出結構化摘要，格式:
「[主要催化劑類型] 核心事件摘要。對{ticker}的短期/中期影響判斷。」"""
        try:
            return await self._generate_with_fallback_async(prompt)
        except:
            return "整合摘要生成失敗"

    def _build_empty_result(self, ticker: str, source_type: str) -> Dict:
        return {
            "ticker": ticker.upper(),
            "timestamp": datetime.now().isoformat(),
            "source_type": source_type,
            "total_fetched": 0,
            "relevant_count": 0,
            "articles": [],
            "filtered_articles": [],
            "catalyst_breakdown": {},
            "local_llm_summary": "今日無重大新聞。",
            "aggregate_sentiment_score": 0.5
        }


def fetch_news_and_distill(ticker: str, username: str, archive: ArchiveManager, api_key: Optional[str] = None) -> Optional[Dict]:
    """
    同步包裝的方法，供 Streamlit 介面調用。
    包含抓取資料與本地 LLM 推理，並自動進行快取儲存。
    """
    ticker = ticker.upper()
    try:
        t_obj = yf.Ticker(ticker)
        raw_news = t_obj.news or []
    except Exception as e:
        logger.error(f"抓取 {ticker} 新聞失敗: {e}")
        raw_news = []

    source_type = "Specific"
    if len(raw_news) < 3:
        logger.info(f"{ticker} 新聞數量不足 (<3)，改抓大盤 SPY。")
        source_type = "Market_Fallback"
        try:
             raw_news = yf.Ticker("SPY").news or []
        except:
             raw_news = []

    # 限制 5 篇避免超過 Token 與等待時間
    top_news = raw_news[:5]

    # 進入 Async 迴圈跑 Local Inference
    client = LocalInferenceClient(api_key=api_key)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result_json = loop.run_until_complete(client.distill_news_batch(ticker, source_type, top_news))

    # 存入快取: storage/users/{username}/news_cache/{ticker}_{YYYYMMDD}.json
    today_str = datetime.now().strftime("%Y-%m-%d")
    archive.save_json(
        username=username,
        category="news_cache",
        filename=f"{ticker}_{today_str}",
        data=result_json
    )
    return result_json
