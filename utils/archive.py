"""
ArchiveManager - 混合式存儲版 (Shared + Multi-User)
支援中心化數據抓取與個人化蒸餾數據隔離。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, Dict, Any

# 專案根目錄設定
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STORAGE_ROOT = _PROJECT_ROOT / "storage"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

class ArchiveManager:
    """
    儲存結構：
    storage/
    ├── shared/                # 中心化公用數據 (所有使用者共用)
    │   ├── raw_data/          # 原始 yfinance CSV (e.g., AAPL_2026-03-18.csv)
    │   └── indicators/        # 標準化技術指標 (e.g., AAPL_common.json)
    └── users/                 # 個人化私有數據 (使用者隔離)
        └── {username}/
            ├── profiles/      # strategy.json (風險、風格、API Key)
            ├── portfolio/     # transactions.json (交易紀錄、觀察清單)
            ├── reports/       # {TICKER}/{DATE}/ AI 分析報告
            └── cache/         # 根據個人策略蒸餾後的數據 (e.g., AAPL_distilled.json)
    """

    def __init__(self, root: Union[str, Path, None] = None):
        self.root = Path(root) if root else _STORAGE_ROOT
        self.shared_base = self.root / "shared"
        self.users_base = self.root / "users"
        
        # 初始化基礎目錄
        for p in [self.shared_base, self.users_base]:
            p.mkdir(parents=True, exist_ok=True)
            
        logger.info("ArchiveManager 初始化成功，儲存根路徑: %s", self.root)

    # --- 中心化數據路徑 (Shared Layer) ---

    def get_shared_path(self, category: str, ticker: str = "") -> Path:
        """
        取得公用數據路徑。
        category: 'raw_data' 或 'indicators'
        """
        path = self.shared_base / category
        if ticker:
            path = path / ticker.upper()
        path.mkdir(parents=True, exist_ok=True)
        return path

    # --- 個人化數據路徑 (User Layer) ---

    def get_user_dir(self, username: str, category: str = "") -> Path:
        """取得特定使用者的分類目錄。範例：storage/users/nasa/cache"""
        user_path = self.users_base / username.lower()
        target_path = user_path / category if category else user_path
        target_path.mkdir(parents=True, exist_ok=True)
        return target_path

    def get_report_path(self, username: str, ticker: str, date: Union[str, datetime]) -> Path:
        """取得 AI 報告的特定日期資料夾。"""
        if isinstance(date, datetime):
            date = date.strftime("%Y-%m-%d")
        
        base = self.get_user_dir(username, "reports")
        ticker_clean = str(ticker).upper().replace("/", "_")
        folder = base / ticker_clean / date
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # --- 通用讀寫介面 ---

    def _build_filepath(self, 
                        is_shared: bool, 
                        username: str, 
                        category: str, 
                        filename: str, 
                        ticker: str = "", 
                        date: Union[str, datetime] = "") -> Path:
        """內部工具：根據類型與使用者生成完整檔案路徑。"""
        if is_shared:
            base = self.get_shared_path(category, ticker)
        elif category == "reports":
            base = self.get_report_path(username, ticker, date)
        else:
            base = self.get_user_dir(username, category)
            
        # 確保檔名包含副檔名
        ext = ".json" if category != "raw_data" else ".csv"
        if "." not in filename:
            filename += ext
        return base / filename

    def save_json(self, username: str, category: str, filename: str, data: Dict, 
                  is_shared: bool = False, ticker: str = "", date: Union[str, datetime] = "") -> Path:
        fp = self._build_filepath(is_shared, username, category, filename, ticker, date)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return fp

    def load_json(self, username: str, category: str, filename: str, 
                  is_shared: bool = False, ticker: str = "", date: Union[str, datetime] = "") -> Optional[Dict]:
        fp = self._build_filepath(is_shared, username, category, filename, ticker, date)
        if not fp.exists():
            return None
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"讀取 JSON 失敗 {fp}: {e}")
            return None

    def save_text(self, username: str, category: str, filename: str, text: str, 
                  ticker: str = "", date: Union[str, datetime] = "") -> Path:
        fp = self._build_filepath(False, username, category, filename, ticker, date)
        fp.write_text(text, encoding="utf-8")
        return fp

    def load_text(self, username: str, category: str, filename: str, 
                  ticker: str = "", date: Union[str, datetime] = "") -> Optional[str]:
        fp = self._build_filepath(False, username, category, filename, ticker, date)
        return fp.read_text(encoding="utf-8") if fp.exists() else None

    # --- 使用者策略與投資組合專用介面 ---

    def load_strategy(self, username: str) -> Dict[str, Any]:
        """讀取使用者策略，若不存在則回傳預設模板。"""
        strategy = self.load_json(username, "profiles", "strategy")
        if strategy:
            return strategy
        
        # 預設策略模板
        return {
            "risk_tolerance": "一般",    # 高 / 一般 / 低
            "trading_style": "一般",     # 激進 / 一般 / 保守
            "trading_frequency": "長期", # 短線 / 長期
            "gemini_api_key": ""
        }

    def save_strategy(self, username: str, strategy_data: Dict):
        """儲存使用者策略。"""
        return self.save_json(username, "profiles", "strategy", strategy_data)