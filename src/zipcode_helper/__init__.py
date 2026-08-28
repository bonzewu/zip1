"""台灣地址郵遞區號查詢小幫手。

以 zip5.5432.tw 提供的公開 API,將台灣中文地址轉換為 3+3 六碼郵遞區號。
"""

from zipcode_helper.models import (
    Failure,
    FailureKind,
    Outcome,
    QueryConfig,
    Success,
)

__version__ = "1.0.0"

__all__ = [
    "Failure",
    "FailureKind",
    "Outcome",
    "QueryConfig",
    "Success",
    "__version__",
]
