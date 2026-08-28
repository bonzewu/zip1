"""端對端測試。

分成兩種:
1. 以真實子行程執行 CLI,但把 API 指向本機的假 HTTP 伺服器
   (走真實 socket、真實 argparse、真實離開碼,不需外網,預設執行)。
2. 連線真正的 zip5.5432.tw(標記為 ``e2e``,需加 ``--run-e2e`` 才會跑)。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def run_cli(args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    """以子行程執行 CLI。"""
    return subprocess.run(
        [sys.executable, "-m", "zipcode_helper", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
        env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
    )


# --------------------------------------------------------------------------
# 對本機假 API 的端對端測試
# --------------------------------------------------------------------------


class TestAgainstLocalServer:
    """走完整子行程流程,但不需要外部網路。"""

    def test_查詢單一地址(self, fake_api_url: str) -> None:
        result = run_cli(
            ["--base-url", fake_api_url, "-d", "0", "台北市信義區市府路1號"]
        )

        assert result.returncode == 0
        assert result.stdout == "110204\n"

    def test_批次查詢與離開碼(self, fake_api_url: str) -> None:
        result = run_cli(
            [
                "--base-url",
                fake_api_url,
                "-d",
                "0",
                "台北市信義區市府路1號",
                "不存在的亂碼地址xyz",
            ]
        )

        assert result.returncode == 1
        assert result.stdout == "110204\n"
        assert "查無此地址" in result.stderr

    def test_管線輸入(self, fake_api_url: str) -> None:
        result = run_cli(
            ["--base-url", fake_api_url, "-d", "0", "-f", "-"],
            stdin="台北市信義區市府路1號\n臺北市大安區羅斯福路四段1號\n",
        )

        assert result.returncode == 0
        assert result.stdout == "110204\n10617\n"

    def test_JSON_輸出可被解析(self, fake_api_url: str) -> None:
        result = run_cli(
            ["--base-url", fake_api_url, "-d", "0", "-j", "台北市信義區市府路1號"]
        )

        data = json.loads(result.stdout)
        assert data[0]["zipcode6"] == "110204"

    def test_無法連線的端點會回報網路錯誤(self) -> None:
        # 127.0.0.1:1 幾乎不可能有服務在聽
        result = run_cli(
            [
                "--base-url",
                "http://127.0.0.1:1/zip5json.py",
                "-d",
                "0",
                "台北市信義區市府路1號",
            ]
        )

        assert result.returncode == 1
        assert "無法連線" in result.stderr


class TestCliBasics:
    """不需要 API 的基本 CLI 行為。"""

    def test_help_可正常顯示(self) -> None:
        result = run_cli(["--help"])

        assert result.returncode == 0
        assert "台灣郵遞區號小幫手" in result.stdout

    def test_version_可正常顯示(self) -> None:
        result = run_cli(["--version"])

        assert result.returncode == 0
        assert "1.0.0" in result.stdout

    def test_未知參數以離開碼二結束(self) -> None:
        result = run_cli(["--不存在的參數"])

        assert result.returncode == 2


# --------------------------------------------------------------------------
# 對真實 API 的端對端測試(需 --run-e2e)
# --------------------------------------------------------------------------


@pytest.mark.e2e
class TestAgainstRealApi:
    """實際連線 zip5.5432.tw 驗證。

    注意:官方要求查詢之間留 2~3 秒緩衝、一天不超過 2000 次,
    因此這裡刻意只保留少量案例,且維持預設節流。
    """

    @pytest.fixture(autouse=True)
    def _throttle(self) -> Iterator[None]:
        """每個真實查詢之後暫停,遵守站方的緩衝時間要求。"""
        yield
        time.sleep(2.5)

    def test_已知地址可查得六碼(self) -> None:
        result = run_cli(["台北市信義區市府路1號"])

        assert result.returncode == 0
        zipcode = result.stdout.strip()
        assert len(zipcode) == 6
        assert zipcode.isdigit()
        assert zipcode.startswith("110")

    def test_亂碼地址查無結果(self) -> None:
        result = run_cli(["這不是一個真的地址zzz999"])

        assert result.returncode == 1
        assert result.stdout == ""
