"""zip5.5432.tw API 的存取層。

設計原則:把「純粹的資料轉換」與「會產生副作用的網路 I/O」切開,
所有解析邏輯都是不依賴網路的純函式,方便單元測試;真正碰網路的
只有 `fetch_raw` 一個函式。
"""

from __future__ import annotations

import functools
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from zipcode_helper.models import (
    Failure,
    FailureKind,
    Outcome,
    QueryConfig,
    Success,
)

logger = logging.getLogger(__name__)

#: 注入用的抓取函式型別:(網址, 逾時秒數, User-Agent) -> 回應內容(bytes)
FetchFn = Callable[[str, float, str], bytes]


# --------------------------------------------------------------------------
# 純函式:網址組裝與回應解析
# --------------------------------------------------------------------------


def build_url(address: str, base_url: str) -> str:
    """組裝查詢網址。

    Args:
        address: 台灣中文地址。
        base_url: API 端點。

    Returns:
        已完成 URL 編碼的完整查詢網址。

    Examples:
        >>> build_url("台北市信義區市府路1號", "https://example.tw/q")
        'https://example.tw/q?adrs=%E5%8F%B0%E5%8C%97%E5%B8%82%E4%BF%A1%E7%BE%A9%E5%8D%80%E5%B8%82%E5%BA%9C%E8%B7%AF1%E8%99%9F'
    """
    query = urllib.parse.urlencode({"adrs": address}, encoding="utf-8")
    return f"{base_url}?{query}"


def strip_zipcode_prefix(prefixed_address: str, zipcode: str) -> str:
    """移除 API 回傳地址前方的郵遞區號前綴。

    API 的 `new_adrs` / `new_adrs6` 欄位會把郵遞區號黏在地址最前面
    (例如 ``110204臺北市信義區市府路1號``),這裡把它還原成純地址。

    Args:
        prefixed_address: 可能帶有郵遞區號前綴的地址。
        zipcode: 對應的郵遞區號;為空字串時原樣回傳。

    Returns:
        去除前綴後的地址。
    """
    if zipcode and prefixed_address.startswith(zipcode):
        return prefixed_address[len(zipcode) :]
    return prefixed_address


def parse_payload(address: str, payload: Any) -> Outcome:
    """把 API 的 JSON 內容轉換成查詢結果。

    Args:
        address: 使用者輸入的原始地址(用於回填結果)。
        payload: 已解析的 JSON 物件,預期為 dict。

    Returns:
        查得郵遞區號時回傳 :class:`Success`;結構不符或完全查不到時
        回傳對應的 :class:`Failure`。
    """
    if not isinstance(payload, dict):
        logger.warning("API 回應不是 JSON 物件:type=%s", type(payload).__name__)
        return Failure(
            address=address,
            kind=FailureKind.DECODE,
            message="API 回應格式不正確(不是 JSON 物件)",
        )

    zipcode6 = str(payload.get("zipcode6", "") or "").strip()
    zipcode5 = str(payload.get("zipcode", "") or "").strip()

    if not zipcode6 and not zipcode5:
        logger.info("查無郵遞區號:%s", address)
        return Failure(
            address=address,
            kind=FailureKind.NOT_FOUND,
            message="查無此地址的郵遞區號,請確認縣市、鄉鎮市區與門牌是否完整",
        )

    # 六碼的標準化地址較貼近原始輸入,優先採用;沒有六碼時退回五碼欄位
    if zipcode6:
        normalized = strip_zipcode_prefix(
            str(payload.get("new_adrs6", "") or ""), zipcode6
        )
    else:
        normalized = strip_zipcode_prefix(
            str(payload.get("new_adrs", "") or ""), zipcode5
        )

    result = Success(
        address=address,
        zipcode6=zipcode6,
        zipcode5=zipcode5,
        normalized_address=normalized or address,
        dataver6=str(payload.get("dataver6", "") or ""),
        dataver5=str(payload.get("dataver", "") or ""),
    )
    logger.debug("解析成功:%s -> 六碼=%r 五碼=%r", address, zipcode6, zipcode5)
    return result


def parse_response(address: str, raw: bytes) -> Outcome:
    """解碼 HTTP 回應內容並轉成查詢結果。

    Args:
        address: 使用者輸入的原始地址。
        raw: API 回應的原始位元組(UTF-8 編碼的 JSON)。

    Returns:
        對應的 :class:`Success` 或 :class:`Failure`。
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        logger.warning("API 回應無法以 UTF-8 解碼:%s", exc)
        return Failure(
            address=address,
            kind=FailureKind.DECODE,
            message="API 回應編碼異常,無法以 UTF-8 解讀",
        )
    except json.JSONDecodeError as exc:
        logger.warning("API 回應不是合法 JSON:%s", exc)
        return Failure(
            address=address,
            kind=FailureKind.DECODE,
            message=f"API 回應不是合法的 JSON:{exc.msg}",
        )
    return parse_payload(address, payload)


# --------------------------------------------------------------------------
# I/O 邊界:實際發出 HTTP 請求
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def build_ssl_context() -> ssl.SSLContext:
    """建立 HTTPS 連線用的 SSL 內容。

    macOS 上以 python.org 安裝套件裝的 Python,若未執行過
    ``Install Certificates.command``,系統預設找不到 CA 憑證而導致
    憑證驗證失敗。因此這裡優先採用 certifi 提供的憑證庫(若已安裝),
    找不到時再退回系統預設設定。

    Returns:
        設定好的 :class:`ssl.SSLContext`(結果會被快取重複使用)。
    """
    try:
        import certifi
    except ImportError:
        logger.debug("未安裝 certifi,使用系統預設 CA 憑證")
        return ssl.create_default_context()

    logger.debug("使用 certifi 提供的 CA 憑證:%s", certifi.where())
    return ssl.create_default_context(cafile=certifi.where())


def fetch_raw(url: str, timeout: float, user_agent: str) -> bytes:
    """對 API 發出 GET 請求並取回原始回應內容。

    這是本模組唯一會碰到網路的函式,測試時會用假的實作替換掉。

    Args:
        url: 完整查詢網址。
        timeout: 逾時秒數。
        user_agent: User-Agent 標頭內容。

    Returns:
        回應的原始位元組。

    Raises:
        urllib.error.URLError: 連線失敗或 HTTP 狀態碼異常。
        TimeoutError: 連線或讀取逾時。
    """
    # 說明:網址一律由 build_url() 從設定中的 http/https 端點組出,
    # 不會出現 file: 或自訂 scheme,因此以下兩處抑制 S310 的來源檢查警告。
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    # 只有 HTTPS 需要 SSL 內容,本機測試用的 http:// 不必建立
    context = build_ssl_context() if url.startswith("https://") else None
    logger.debug("送出請求:%s (timeout=%.1fs)", url, timeout)
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=timeout, context=context
    ) as response:
        return response.read()


# --------------------------------------------------------------------------
# 組合層:單筆與批次查詢
# --------------------------------------------------------------------------


def query_address(
    address: str,
    config: QueryConfig | None = None,
    fetch: FetchFn = fetch_raw,
) -> Outcome:
    """查詢單一地址的郵遞區號。

    Args:
        address: 台灣中文地址。
        config: 查詢設定,未指定時使用預設值。
        fetch: 抓取函式,預設為真正的 HTTP 請求;測試可注入假實作。

    Returns:
        對應的 :class:`Success` 或 :class:`Failure`,本函式不會拋出例外。
    """
    settings = config or QueryConfig()
    cleaned = address.strip()

    if not cleaned:
        logger.info("略過空白地址")
        return Failure(
            address=address,
            kind=FailureKind.EMPTY_ADDRESS,
            message="地址不可為空白",
        )

    url = build_url(cleaned, settings.base_url)

    try:
        raw = fetch(url, settings.timeout, settings.user_agent)
    except (TimeoutError, ssl.SSLError, urllib.error.URLError) as exc:
        # URLError 會把真正的原因包在 reason 裡(逾時、憑證錯誤都是如此),需一併判別
        reason = getattr(exc, "reason", exc)
        if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
            logger.warning("查詢逾時(%.1fs):%s", settings.timeout, cleaned)
            return Failure(
                address=address,
                kind=FailureKind.TIMEOUT,
                message=f"查詢逾時(超過 {settings.timeout:.0f} 秒),請稍後再試",
            )
        if isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError):
            logger.warning("TLS 憑證驗證失敗:%s(%s)", cleaned, reason)
            return Failure(
                address=address,
                kind=FailureKind.TLS,
                message=(
                    f"HTTPS 憑證驗證失敗:{reason};"
                    "請安裝 certifi(uv pip install certifi),"
                    "或執行 Python 安裝目錄中的 Install Certificates.command"
                ),
            )
        logger.warning("連線失敗:%s(%s)", cleaned, reason)
        return Failure(
            address=address,
            kind=FailureKind.NETWORK,
            message=f"無法連線至郵遞區號 API:{reason}",
        )
    except OSError as exc:  # pragma: no cover - 防禦性處理罕見的底層 I/O 錯誤
        logger.warning("網路 I/O 發生錯誤:%s(%s)", cleaned, exc)
        return Failure(
            address=address,
            kind=FailureKind.NETWORK,
            message=f"網路發生錯誤:{exc}",
        )

    return parse_response(cleaned, raw)


def query_addresses(
    addresses: Iterable[str],
    config: QueryConfig | None = None,
    fetch: FetchFn = fetch_raw,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Outcome]:
    """依序查詢多個地址,並在每次查詢之間插入間隔。

    以產生器回傳,呼叫端可邊查邊輸出,不必等全部完成。

    Args:
        addresses: 地址集合。
        config: 查詢設定,未指定時使用預設值。
        fetch: 抓取函式,測試可注入假實作。
        sleep: 等待函式,測試可注入假實作以免真的睡著。

    Yields:
        每個地址對應的 :class:`Success` 或 :class:`Failure`。
    """
    settings = config or QueryConfig()

    for index, address in enumerate(addresses):
        # 間隔只加在「兩次實際請求之間」,第一筆不必等
        if index > 0 and settings.delay > 0:
            logger.debug("等待 %.1f 秒後查詢下一筆", settings.delay)
            sleep(settings.delay)
        yield query_address(address, settings, fetch)
