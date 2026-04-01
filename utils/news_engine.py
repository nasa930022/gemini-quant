"""
news_engine.py - Edge-Cloud Hybrid 新聞分析抓取與本地推理引擎 (Mock版)
1. 抓取 yfinance 新聞，若不足3篇則抓取 SPY。
2. 平行拋給 Edge Local LLM (目前以 Gemini 模擬)。
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import yfinance as yf
from google import genai

from .archive import ArchiveManager

logger = logging.getLogger(__name__)

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
        self.model_name = "gemini-3.1-flash-lite-preview" 

    async def _async_analyze_article(self, article: Dict) -> Dict:
        """非同步逐層提煉單篇文章情緒"""
        if not self.client:
            return {"title": article.get("title", ""), "sentiment": 0.5, "key_point": "API Key缺失"}

        title = article.get("title", "無標題")
        prompt = f"""
        角色: 金融快訊情緒標註員 (Local Pre-filter)
        任務: 分析以下新聞標題與摘要（若有），提取其核心情緒分數與關鍵影響。
        
        新聞標題: {title}
        
        **輸出要求 (僅輸出嚴格 JSON)**
        {{
            "sentiment": 0.0到1.0之間的分數 (0=極端看空, 0.5=中立, 1.0=極端看多),
            "key_point": "用15字內中文摘要核心亮點或利空"
        }}
        """
        try:
            # 取代為 vLLM async client 呼叫時，此段以 async await 執行
            # Gemini sdk 也有 async API (client.aio.models.generate_content)
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            text = response.text.strip().replace("```json", "").replace("```", "")
            data_dict = json.loads(text)
            return {
                "title": title,
                "sentiment": float(data_dict.get("sentiment", 0.5)),
                "key_point": data_dict.get("key_point", "分析失敗")
            }
        except Exception as e:
            logger.error(f"分析文章失敗: {e}")
            return {"title": title, "sentiment": 0.5, "key_point": "分析發生錯誤"}

    async def distill_news_batch(self, ticker: str, source_type: str, articles: List[Dict]) -> Dict:
        """接收多篇文章並行蒸餾，最後產生總結"""
        if not articles:
            return self._build_empty_result(ticker, source_type)

        # 1. 平行處理所有文章的情緒提煉
        tasks = [self._async_analyze_article(a) for a in articles]
        processed_articles = await asyncio.gather(*tasks)

        # 2. 計算總體情緒分數
        valid_scores = [a["sentiment"] for a in processed_articles if "sentiment" in a]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0.5

        # 3. 生成統合摘要 (Local LLM Summary)
        summary = await self._generate_aggregate_summary(ticker, processed_articles, avg_score)

        return {
            "ticker": ticker.upper(),
            "timestamp": datetime.now().isoformat(),
            "source_type": source_type,
            "articles": processed_articles,
            "local_llm_summary": summary,
            "aggregate_sentiment_score": round(avg_score, 2)
        }

    async def _generate_aggregate_summary(self, ticker: str, processed_articles: List[Dict], avg_score: float) -> str:
        """基於蒸餾後的重點，生成綜合摘要。"""
        if not self.client:
            return "無法生成摘要 (未登入或無 API Key)"
        
        points = [f"- {a['key_point']}" for a in processed_articles]
        points_text = "\n".join(points)
        prompt = f"""
        角色: 金融快訊主編。標的: {ticker}
        以下是 5 篇重點新聞提煉，請以「整體情緒分數為 {avg_score:.2f} (0=極端看空,1=看多)」為前提，
        用 50 字以內的中文寫出核心市場催化劑 (Catalyst) 總結：
        {points_text}
        """
        try:
            response = await self.client.aio.models.generate_content(model=self.model_name, contents=prompt)
            return response.text.strip()
        except:
            return "整合摘要生成失敗"

    def _build_empty_result(self, ticker: str, source_type: str) -> Dict:
        return {
            "ticker": ticker.upper(),
            "timestamp": datetime.now().isoformat(),
            "source_type": source_type,
            "articles": [],
            "local_llm_summary": "今日無重大新聞。",
            "aggregate_sentiment_score": 0.5
        }


def fetch_news_and_distill(ticker: str, username: str, archive: ArchiveManager, api_key: Optional[str] = None) -> Optional[Dict]:
    """
    同步包裝的方法，供 Streamlit 介面調用。
    包含抓取資料與本地 LLM 推理，並自動進行 快取儲存。
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

