"""資料模型定義。

本模組只放不可變(immutable)的資料型別,不包含任何 I/O 或副作用,
讓查詢結果可以在各層之間安全傳遞與比較。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

#: API 預設端點,可透過 QueryConfig 覆寫(測試時會指向本機假伺服器)
DEFAULT_BASE_URL = "https://zip5.5432.tw/zip5json.py"

#: 依 API 官方說明「查詢間請留 2~3 秒緩衝」,批次查詢的預設間隔秒數
DEFAULT_DELAY_SECONDS = 2.0

#: 單次 HTTP 請求的預設逾時秒數
DEFAULT_TIMEOUT_SECONDS = 10.0


class FailureKind(Enum):
    """查詢失敗的分類,供 CLI 決定離開碼與提示訊息。"""

    EMPTY_ADDRESS = "empty_address"  # 使用者輸入空白地址
    NOT_FOUND = "not_found"  # API 有回應,但查不到任何郵遞區號
    NETWORK = "network"  # 連線失敗、DNS 錯誤、HTTP 狀態碼異常
    TIMEOUT = "timeout"  # 連線或讀取逾時
    TLS = "tls"  # HTTPS 憑證驗證失敗(常見於未安裝 CA 憑證的環境)
    DECODE = "decode"  # 回應不是預期的 JSON 結構


@dataclass(frozen=True)
class Success:
    """查詢成功的結果。

    注意:`zipcode6` 有可能是空字串。zip5.5432.tw 的 3+3 資料涵蓋範圍
    小於 3+2,部分地址只查得到五碼,此時仍視為「成功」,由呈現層決定
    要不要降級顯示五碼。

    Attributes:
        address: 使用者輸入的原始地址。
        zipcode6: 3+3 六碼郵遞區號;查無六碼時為空字串。
        zipcode5: 3+2 五碼郵遞區號;查無五碼時為空字串。
        normalized_address: API 標準化後的地址(已去除前綴郵遞區號)。
        dataver6: 六碼所使用的郵局資料版號。
        dataver5: 五碼所使用的郵局資料版號。
    """

    address: str
    zipcode6: str
    zipcode5: str
    normalized_address: str
    dataver6: str = ""
    dataver5: str = ""

    @property
    def has_six_digits(self) -> bool:
        """是否查到完整的六碼郵遞區號。"""
        return bool(self.zipcode6)

    @property
    def best_zipcode(self) -> str:
        """可用的最佳郵遞區號:優先六碼,退而求其次為五碼。"""
        return self.zipcode6 or self.zipcode5


@dataclass(frozen=True)
class Failure:
    """查詢失敗的結果。

    Attributes:
        address: 使用者輸入的原始地址。
        kind: 失敗分類。
        message: 適合直接顯示給使用者的中文訊息。
    """

    address: str
    kind: FailureKind
    message: str


#: 單筆查詢的結果型別(成功或失敗)
Outcome = Union[Success, Failure]


@dataclass(frozen=True)
class QueryConfig:
    """查詢行為設定。

    Attributes:
        base_url: API 端點網址。
        timeout: 單次請求逾時秒數。
        delay: 批次查詢時每筆之間的間隔秒數,用來遵守 API 的使用禮儀。
        user_agent: 送出的 User-Agent 標頭。
    """

    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    delay: float = DEFAULT_DELAY_SECONDS
    user_agent: str = "zipcode-helper/1.0 (+https://github.com/bonzewu/zip1)"
