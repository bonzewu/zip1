"""pytest 共用設定與測試替身(fixtures)。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# --------------------------------------------------------------------------
# e2e 測試開關:預設略過,加上 --run-e2e 才會連線真實 API
# --------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """新增 ``--run-e2e`` 選項。"""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="執行需要連線 zip5.5432.tw 的端對端測試",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """未指定 ``--run-e2e`` 時,自動略過標記為 e2e 的測試。"""
    if config.getoption("--run-e2e"):
        return

    skip_marker = pytest.mark.skip(reason="需要網路,請加上 --run-e2e 才會執行")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_marker)


# --------------------------------------------------------------------------
# 假的 API 回應
# --------------------------------------------------------------------------

#: 六碼與五碼都查得到的地址(取自真實 API 回應)
PAYLOAD_FULL = {
    "zipcode6": "110204",
    "dataver6": "11505",
    "adrs": "台北市信義區市府路1號",
    "new_adrs6_2": "110204台北市信義區市府路1號",
    "new_adrs6": "110204臺北市信義區市府路1號",
    "new_adrs2": "11008台北市信義區市府路1號",
    "new_adrs": "11008臺北市信義區市府路1號",
    "dataver": "11208",
    "zipcode": "11008",
}

#: 只查得到五碼的地址(3+3 資料未涵蓋)
PAYLOAD_FIVE_ONLY = {
    "zipcode6": "",
    "dataver6": "11505",
    "adrs": "臺北市大安區羅斯福路四段1號",
    "new_adrs6": "臺北市大安區羅斯福路四段1號",
    "new_adrs2": "10617臺北市大安區羅斯福路四段1號",
    "new_adrs": "10617臺北市大安區羅斯福路４段1號",
    "dataver": "11208",
    "zipcode": "10617",
}

#: 完全查不到的地址
PAYLOAD_NOT_FOUND = {
    "zipcode6": "",
    "dataver6": "11505",
    "adrs": "不存在的亂碼地址xyz",
    "new_adrs6": "不存在的亂碼地址xyz",
    "new_adrs2": "不存在的亂碼地址xyz",
    "new_adrs": "不存在的亂碼地址xyz",
    "dataver": "11208",
    "zipcode": "",
}


def encode(payload: dict) -> bytes:
    """把字典編碼成 API 實際回傳的 UTF-8 JSON 位元組。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class FakeFetcher:
    """可注入的假抓取函式,依網址中的地址關鍵字回傳對應假資料。

    同時記錄每次呼叫的網址,方便驗證查詢行為。
    """

    def __init__(self, responses: dict[str, bytes] | None = None) -> None:
        self.calls: list[str] = []
        self.responses = responses or {
            "市府路": encode(PAYLOAD_FULL),
            "羅斯福路": encode(PAYLOAD_FIVE_ONLY),
        }
        self.default = encode(PAYLOAD_NOT_FOUND)

    def __call__(self, url: str, timeout: float, user_agent: str) -> bytes:
        import urllib.parse

        self.calls.append(url)
        decoded = urllib.parse.unquote(url)
        for keyword, body in self.responses.items():
            if keyword in decoded:
                return body
        return self.default


@pytest.fixture
def fake_fetch() -> FakeFetcher:
    """提供預設的假抓取函式。"""
    return FakeFetcher()


@pytest.fixture
def no_sleep():
    """提供不會真的等待的 sleep 替身,並記錄被要求等待的秒數。"""
    slept: list[float] = []
    return slept.append, slept


# --------------------------------------------------------------------------
# 本機假 API 伺服器(供需要真實 socket 的測試共用)
# --------------------------------------------------------------------------


class FakeApiHandler(BaseHTTPRequestHandler):
    """依查詢字串中的地址關鍵字回傳對應的假郵遞區號資料。"""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的介面
        query = urllib.parse.urlparse(self.path).query
        address = urllib.parse.parse_qs(query).get("adrs", [""])[0]

        if "市府路" in address:
            payload = PAYLOAD_FULL
        elif "羅斯福路" in address:
            payload = PAYLOAD_FIVE_ONLY
        else:
            payload = PAYLOAD_NOT_FOUND

        body = encode(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """關閉預設的請求日誌,保持測試輸出乾淨。"""


@pytest.fixture(scope="session")
def fake_api_url() -> Iterator[str]:
    """啟動本機假 API 伺服器,回傳其端點網址。"""
    server = HTTPServer(("127.0.0.1", 0), FakeApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}/zip5json.py"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
