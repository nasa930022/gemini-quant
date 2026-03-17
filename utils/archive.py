"""
ArchiveManager - 依 ticker/date 管理本地 JSON 數據存儲。
支援 WSL2 路徑相容、日誌記錄。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

# WSL2 相容：以專案根目錄為基準，確保 analytics_db 路徑正確
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ROOT_DEFAULT = _PROJECT_ROOT / "analytics_db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class ArchiveManager:
    """
    依 ticker / date 組織的本地 JSON 存儲管理員。
    預設根目錄：/home/nasa/work/investment/analytics_db/
    """

    def __init__(self, root: Union[str, Path, None] = None):
        self.root = Path(root) if root else _ROOT_DEFAULT
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info("ArchiveManager 已初始化，根路徑: %s", self.root)

    def get_path(self, ticker: str, date: Union[str, datetime]) -> Path:
        """
        依 ticker 和 date 取得/建立資料夾路徑。
        date 可為 'YYYY-MM-DD' 或 datetime。
        """
        if isinstance(date, datetime):
            date = date.strftime("%Y-%m-%d")
        ticker_clean = str(ticker).upper().replace("/", "_")
        folder = self.root / ticker_clean / date
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _filepath(self, ticker: str, date: Union[str, datetime], filename: str) -> Path:
        base = self.get_path(ticker, date)
        # 若已帶副檔名則直接使用，否則預設為 .json
        name = filename if "." in filename else f"{filename}.json"
        return base / name

    def save_json(
        self,
        ticker: str,
        date: Union[str, datetime],
        filename: str,
        data: dict,
    ) -> Path:
        """將 dict 存成格式化的 JSON 檔。"""
        fp = self._filepath(ticker, date, filename)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("已寫入: %s", fp)
        return fp

    def load_json(
        self,
        ticker: str,
        date: Union[str, datetime],
        filename: str,
    ) -> Optional[dict]:
        """讀取指定 JSON，若不存在則回傳 None。"""
        fp = self._filepath(ticker, date, filename)
        if not fp.exists():
            logger.debug("檔案不存在: %s", fp)
            return None
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("已讀取: %s", fp)
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("讀取失敗 %s: %s", fp, e)
            return None

    def exists(self, ticker: str, date: Union[str, datetime], filename: str) -> bool:
        """檢查該數據檔是否已存在，可用於數據復用判斷。"""
        fp = self._filepath(ticker, date, filename)
        return fp.exists()

    def save_text(
        self,
        ticker: str,
        date: Union[str, datetime],
        filename: str,
        text: str,
    ) -> Path:
        """以 UTF-8 儲存任意文字檔（例如 Markdown 報告）。"""
        fp = self._filepath(ticker, date, filename)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info("已寫入文字檔: %s", fp)
        return fp

    def load_text(
        self,
        ticker: str,
        date: Union[str, datetime],
        filename: str,
    ) -> Optional[str]:
        """讀取文字檔，若不存在則回傳 None。"""
        fp = self._filepath(ticker, date, filename)
        if not fp.exists():
            logger.debug("文字檔不存在: %s", fp)
            return None
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = f.read()
            logger.info("已讀取文字檔: %s", fp)
            return data
        except IOError as e:
            logger.warning("讀取文字檔失敗 %s: %s", fp, e)
            return None
