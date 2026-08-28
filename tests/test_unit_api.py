"""單元測試:api 模組的純函式與錯誤處理。"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.parse

import pytest

from conftest import (
    PAYLOAD_FIVE_ONLY,
    PAYLOAD_FULL,
    PAYLOAD_NOT_FOUND,
    encode,
)
from zipcode_helper.api import (
    build_ssl_context,
    build_url,
    fetch_raw,
    parse_payload,
    parse_response,
    query_address,
    query_addresses,
    strip_zipcode_prefix,
)
from zipcode_helper.models import Failure, FailureKind, QueryConfig, Success


class TestBuildUrl:
    """網址組裝。"""

    def test_中文地址會被百分比編碼(self) -> None:
        url = build_url("台北市信義區市府路1號", "https://example.tw/q")

        assert url.startswith("https://example.tw/q?adrs=")
        assert "台北市" not in url  # 必須已編碼
        query = urllib.parse.urlparse(url).query
        assert urllib.parse.parse_qs(query)["adrs"] == ["台北市信義區市府路1號"]

    def test_特殊字元不會破壞查詢字串(self) -> None:
        url = build_url("桃園市中壢區中大路300號&test=1", "https://example.tw/q")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert query["adrs"] == ["桃園市中壢區中大路300號&test=1"]
        assert "test" not in query


class TestStripZipcodePrefix:
    """郵遞區號前綴移除。"""

    def test_移除相符的前綴(self) -> None:
        assert strip_zipcode_prefix("110204臺北市信義區", "110204") == "臺北市信義區"

    def test_前綴不相符時保持原樣(self) -> None:
        assert strip_zipcode_prefix("臺北市信義區", "110204") == "臺北市信義區"

    def test_郵遞區號為空時保持原樣(self) -> None:
        assert strip_zipcode_prefix("臺北市信義區", "") == "臺北市信義區"


class TestParsePayload:
    """API 回應內容解析。"""

    def test_六碼與五碼皆存在(self) -> None:
        result = parse_payload("台北市信義區市府路1號", PAYLOAD_FULL)

        assert isinstance(result, Success)
        assert result.zipcode6 == "110204"
        assert result.zipcode5 == "11008"
        assert result.normalized_address == "臺北市信義區市府路1號"
        assert result.has_six_digits is True
        assert result.best_zipcode == "110204"

    def test_只有五碼時仍算成功但標記為無六碼(self) -> None:
        result = parse_payload("臺北市大安區羅斯福路四段1號", PAYLOAD_FIVE_ONLY)

        assert isinstance(result, Success)
        assert result.zipcode6 == ""
        assert result.zipcode5 == "10617"
        assert result.has_six_digits is False
        assert result.best_zipcode == "10617"
        # 沒有六碼時改用五碼欄位的標準化地址
        assert result.normalized_address == "臺北市大安區羅斯福路４段1號"

    def test_兩者皆無視為查無此地址(self) -> None:
        result = parse_payload("不存在的亂碼地址xyz", PAYLOAD_NOT_FOUND)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.NOT_FOUND

    def test_回應不是物件時回傳解析失敗(self) -> None:
        result = parse_payload("台北市", ["不是物件"])

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.DECODE

    def test_欄位為_None_時視同空字串(self) -> None:
        result = parse_payload("台北市", {"zipcode6": None, "zipcode": "10058"})

        assert isinstance(result, Success)
        assert result.zipcode6 == ""
        assert result.zipcode5 == "10058"

    def test_郵遞區號前後空白會被去除(self) -> None:
        result = parse_payload("台北市", {"zipcode6": " 110204 ", "zipcode": ""})

        assert isinstance(result, Success)
        assert result.zipcode6 == "110204"


class TestParseResponse:
    """位元組層級的回應解析。"""

    def test_正常_JSON(self) -> None:
        result = parse_response("台北市信義區市府路1號", encode(PAYLOAD_FULL))

        assert isinstance(result, Success)
        assert result.zipcode6 == "110204"

    def test_非法_JSON_回傳解析失敗(self) -> None:
        result = parse_response("台北市", b"<html>error</html>")

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.DECODE

    def test_非_UTF8_內容回傳解析失敗(self) -> None:
        result = parse_response("台北市", b"\xff\xfe\x00\x01")

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.DECODE


class TestQueryAddress:
    """單筆查詢的組合行為與錯誤處理。"""

    def test_空白地址不會發出請求(self, fake_fetch) -> None:
        result = query_address("   ", fetch=fake_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.EMPTY_ADDRESS
        assert fake_fetch.calls == []

    def test_地址前後空白會被去除後查詢(self, fake_fetch) -> None:
        result = query_address("  台北市信義區市府路1號  ", fetch=fake_fetch)

        assert isinstance(result, Success)
        assert result.address == "台北市信義區市府路1號"

    def test_使用設定中的_base_url(self, fake_fetch) -> None:
        config = QueryConfig(base_url="https://fake.local/api")
        query_address("台北市信義區市府路1號", config, fetch=fake_fetch)

        assert fake_fetch.calls[0].startswith("https://fake.local/api?adrs=")

    def test_連線失敗回傳網路錯誤(self) -> None:
        def broken_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise urllib.error.URLError("Name or service not known")

        result = query_address("台北市信義區市府路1號", fetch=broken_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.NETWORK
        assert "無法連線" in result.message

    def test_HTTP_狀態碼異常視為網路錯誤(self) -> None:
        def http_error_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

        result = query_address("台北市信義區市府路1號", fetch=http_error_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.NETWORK

    def test_逾時回傳逾時錯誤(self) -> None:
        def slow_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise TimeoutError("timed out")

        result = query_address("台北市信義區市府路1號", fetch=slow_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.TIMEOUT

    def test_憑證驗證失敗會給出可操作的提示(self) -> None:
        def bad_cert_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError("certificate verify failed")
            )

        result = query_address("台北市信義區市府路1號", fetch=bad_cert_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.TLS
        assert "certifi" in result.message

    def test_直接拋出的_SSLError_也會被歸類為憑證問題(self) -> None:
        def bad_cert_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise ssl.SSLError("handshake failure")

        result = query_address("台北市信義區市府路1號", fetch=bad_cert_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.TLS

    def test_包在_URLError_裡的逾時也能辨識(self) -> None:
        def slow_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            raise urllib.error.URLError(TimeoutError("timed out"))

        result = query_address("台北市信義區市府路1號", fetch=slow_fetch)

        assert isinstance(result, Failure)
        assert result.kind is FailureKind.TIMEOUT


class TestQueryAddresses:
    """批次查詢的順序與節流行為。"""

    def test_依序回傳每筆結果(self, fake_fetch, no_sleep) -> None:
        sleep_fn, _ = no_sleep
        addresses = ["台北市信義區市府路1號", "臺北市大安區羅斯福路四段1號"]

        results = list(
            query_addresses(addresses, fetch=fake_fetch, sleep=sleep_fn)
        )

        assert [r.zipcode6 for r in results] == ["110204", ""]

    def test_第一筆之前不等待_之後每筆都等待(self, fake_fetch, no_sleep) -> None:
        sleep_fn, slept = no_sleep
        config = QueryConfig(delay=2.5)

        list(query_addresses(["a", "b", "c"], config, fake_fetch, sleep_fn))

        assert slept == [2.5, 2.5]

    def test_delay_為零時完全不等待(self, fake_fetch, no_sleep) -> None:
        sleep_fn, slept = no_sleep
        config = QueryConfig(delay=0)

        list(query_addresses(["a", "b"], config, fake_fetch, sleep_fn))

        assert slept == []

    def test_以產生器方式惰性查詢(self, fake_fetch, no_sleep) -> None:
        sleep_fn, _ = no_sleep
        results = query_addresses(["a", "b", "c"], fetch=fake_fetch, sleep=sleep_fn)

        next(results)  # 只取第一筆

        assert len(fake_fetch.calls) == 1

    def test_單筆失敗不影響其餘查詢(self, no_sleep) -> None:
        sleep_fn, _ = no_sleep
        calls: list[str] = []

        def flaky_fetch(url: str, timeout: float, user_agent: str) -> bytes:
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.URLError("boom")
            return encode(PAYLOAD_FULL)

        results = list(
            query_addresses(
                ["壞掉的", "台北市信義區市府路1號"],
                fetch=flaky_fetch,
                sleep=sleep_fn,
            )
        )

        assert isinstance(results[0], Failure)
        assert isinstance(results[1], Success)


class TestFetchRaw:
    """真正的 HTTP 抓取函式(對本機假伺服器)。"""

    def test_取回原始_JSON_內容(self, fake_api_url: str) -> None:
        url = build_url("台北市信義區市府路1號", fake_api_url)

        raw = fetch_raw(url, timeout=10.0, user_agent="pytest")

        assert isinstance(raw, bytes)
        assert parse_response("台北市信義區市府路1號", raw) == parse_payload(
            "台北市信義區市府路1號", PAYLOAD_FULL
        )

    def test_連不上的端點會拋出_URLError(self) -> None:
        with pytest.raises(urllib.error.URLError):
            fetch_raw("http://127.0.0.1:1/zip5json.py", 5.0, "pytest")

    def test_整條查詢流程可用真實_socket_完成(self, fake_api_url: str) -> None:
        config = QueryConfig(base_url=fake_api_url)

        result = query_address("台北市信義區市府路1號", config)

        assert isinstance(result, Success)
        assert result.zipcode6 == "110204"


class TestBuildSslContext:
    """SSL 內容建立。"""

    def test_回傳可驗證憑證的內容且已載入_CA(self) -> None:
        build_ssl_context.cache_clear()

        context = build_ssl_context()

        assert context.verify_mode is ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.get_ca_certs(), "應載入至少一張 CA 憑證"

    def test_結果會被快取以免重複讀取憑證檔(self) -> None:
        build_ssl_context.cache_clear()

        assert build_ssl_context() is build_ssl_context()

    def test_缺少_certifi_時退回系統預設(self, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "certifi":
                raise ImportError("no certifi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        build_ssl_context.cache_clear()

        context = build_ssl_context()

        assert isinstance(context, ssl.SSLContext)
        build_ssl_context.cache_clear()


@pytest.mark.parametrize(
    ("payload", "expected_six"),
    [
        (PAYLOAD_FULL, "110204"),
        (PAYLOAD_FIVE_ONLY, ""),
    ],
)
def test_解析結果的六碼欄位(payload: dict, expected_six: str) -> None:
    """以參數化方式覆蓋兩種主要回應樣態。"""
    result = parse_payload(payload["adrs"], payload)

    assert isinstance(result, Success)
    assert result.zipcode6 == expected_six
