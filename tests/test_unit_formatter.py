"""單元測試:formatter 模組的輸出格式。"""

from __future__ import annotations

import json

from zipcode_helper.formatter import (
    format_json,
    format_warning,
    format_zipcode_line,
    outcome_to_dict,
)
from zipcode_helper.models import Failure, FailureKind, Success

FULL = Success(
    address="台北市信義區市府路1號",
    zipcode6="110204",
    zipcode5="11008",
    normalized_address="臺北市信義區市府路1號",
    dataver6="11505",
    dataver5="11208",
)

FIVE_ONLY = Success(
    address="臺北市大安區羅斯福路四段1號",
    zipcode6="",
    zipcode5="10617",
    normalized_address="臺北市大安區羅斯福路４段1號",
)

NOT_FOUND = Failure(
    address="不存在的亂碼地址xyz",
    kind=FailureKind.NOT_FOUND,
    message="查無此地址的郵遞區號,請確認縣市、鄉鎮市區與門牌是否完整",
)


class TestFormatZipcodeLine:
    """stdout 行的格式。"""

    def test_預設只輸出六碼(self) -> None:
        assert format_zipcode_line(FULL, show_address=False) == "110204"

    def test_加上地址時以_Tab_分隔(self) -> None:
        line = format_zipcode_line(FULL, show_address=True)

        assert line == "110204\t臺北市信義區市府路1號"
        assert line.count("\t") == 1

    def test_沒有六碼時降級輸出五碼(self) -> None:
        assert format_zipcode_line(FIVE_ONLY, show_address=False) == "10617"


class TestFormatWarning:
    """stderr 提醒訊息。"""

    def test_六碼齊全時沒有提醒(self) -> None:
        assert format_warning(FULL, strict=False) is None

    def test_只有五碼時提醒降級(self) -> None:
        warning = format_warning(FIVE_ONLY, strict=False)

        assert warning is not None
        assert warning.startswith("警告:")
        assert "10617" in warning

    def test_嚴格模式下只有五碼會標示為錯誤(self) -> None:
        warning = format_warning(FIVE_ONLY, strict=True)

        assert warning is not None
        assert warning.startswith("錯誤:")

    def test_查詢失敗一律標示為錯誤(self) -> None:
        warning = format_warning(NOT_FOUND, strict=False)

        assert warning is not None
        assert warning.startswith("錯誤:")
        assert "查無此地址" in warning

    def test_空白地址的提醒可讀(self) -> None:
        failure = Failure(
            address="", kind=FailureKind.EMPTY_ADDRESS, message="地址不可為空白"
        )

        assert "(空白)" in (format_warning(failure, strict=False) or "")


class TestJsonOutput:
    """JSON 輸出。"""

    def test_成功結果包含完整欄位(self) -> None:
        data = outcome_to_dict(FULL)

        assert data == {
            "ok": True,
            "address": "台北市信義區市府路1號",
            "zipcode6": "110204",
            "zipcode5": "11008",
            "normalized_address": "臺北市信義區市府路1號",
            "dataver6": "11505",
            "dataver5": "11208",
        }

    def test_失敗結果包含錯誤分類(self) -> None:
        data = outcome_to_dict(NOT_FOUND)

        assert data["ok"] is False
        assert data["error"] == "not_found"

    def test_輸出為合法_JSON_陣列且保留中文(self) -> None:
        text = format_json([FULL, NOT_FOUND])

        assert "臺北市" in text  # 未被轉成 \uXXXX
        parsed = json.loads(text)
        assert len(parsed) == 2
        assert parsed[0]["zipcode6"] == "110204"
        assert parsed[1]["ok"] is False

    def test_空清單輸出空陣列(self) -> None:
        assert json.loads(format_json([])) == []
