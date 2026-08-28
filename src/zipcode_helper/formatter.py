"""查詢結果的呈現層。

全部是純函式:輸入結果物件,輸出字串,不做任何列印或 I/O,
讓輸出格式可以被單元測試逐字驗證。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from zipcode_helper.models import Failure, Outcome, Success


def format_zipcode_line(result: Success, show_address: bool) -> str:
    """產生單筆成功結果要輸出到 stdout 的內容。

    Args:
        result: 成功的查詢結果。
        show_address: 是否一併輸出標準化後的地址。

    Returns:
        以 Tab 分隔的一行文字;僅有郵遞區號時不含 Tab。
    """
    zipcode = result.best_zipcode
    if show_address:
        return f"{zipcode}\t{result.normalized_address}"
    return zipcode


def format_warning(outcome: Outcome, strict: bool) -> str | None:
    """產生要輸出到 stderr 的提醒訊息。

    Args:
        outcome: 單筆查詢結果。
        strict: 嚴格模式下,查不到六碼會被視為錯誤而非提醒。

    Returns:
        需要提醒時回傳訊息字串;一切正常則回傳 ``None``。
    """
    if isinstance(outcome, Failure):
        return f"錯誤:{outcome.address or '(空白)'} — {outcome.message}"

    if outcome.has_six_digits:
        return None

    level = "錯誤" if strict else "警告"
    detail = (
        f"僅查得五碼 {outcome.zipcode5}"
        if outcome.zipcode5
        else "查無可用的郵遞區號"
    )
    return (
        f"{level}:{outcome.address} — 此地址查無 3+3 六碼郵遞區號({detail});"
        "郵局 3+3 資料涵蓋範圍小於 3+2,可嘗試補上完整門牌號碼"
    )


def outcome_to_dict(outcome: Outcome) -> dict[str, object]:
    """把查詢結果轉成可序列化為 JSON 的字典。

    Args:
        outcome: 單筆查詢結果。

    Returns:
        含 ``ok`` 欄位的字典;成功與失敗的其餘欄位不同。
    """
    if isinstance(outcome, Failure):
        return {
            "ok": False,
            "address": outcome.address,
            "error": outcome.kind.value,
            "message": outcome.message,
        }

    return {
        "ok": True,
        "address": outcome.address,
        "zipcode6": outcome.zipcode6,
        "zipcode5": outcome.zipcode5,
        "normalized_address": outcome.normalized_address,
        "dataver6": outcome.dataver6,
        "dataver5": outcome.dataver5,
    }


def format_json(outcomes: Sequence[Outcome]) -> str:
    """把多筆查詢結果格式化為 JSON 陣列字串。

    Args:
        outcomes: 查詢結果序列。

    Returns:
        縮排兩格、保留中文字元的 JSON 字串(不含結尾換行)。
    """
    payload = [outcome_to_dict(outcome) for outcome in outcomes]
    return json.dumps(payload, ensure_ascii=False, indent=2)
