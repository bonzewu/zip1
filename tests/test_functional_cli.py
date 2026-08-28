"""功能測試:以注入的假 API 驅動整個 CLI 流程。

不啟動子行程、不連網路,但走完「參數解析 → 查詢 → 輸出 → 離開碼」全流程。
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from conftest import PAYLOAD_FULL, FakeFetcher, encode
from zipcode_helper.cli import clean_address_lines, decide_exit_code, main
from zipcode_helper.models import Failure, FailureKind, Success


class TtyStringIO(io.StringIO):
    """假裝自己是終端機的輸入串流,用來觸發互動模式。"""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def run_cli(fake_fetch):
    """提供執行 CLI 的輔助函式,回傳 (離開碼, stdout, stderr)。"""

    def _run(argv, stdin_text: str = "", tty: bool = False, fetch=None):
        stdin = TtyStringIO(stdin_text) if tty else io.StringIO(stdin_text)
        stdout, stderr = io.StringIO(), io.StringIO()
        # 測試一律關閉節流,避免拖慢測試時間
        code = main(
            [*argv, "--delay", "0"],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            fetch=fetch or fake_fetch,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    return _run


class TestSingleAddress:
    """單一地址查詢。"""

    def test_輸出六碼郵遞區號且離開碼為零(self, run_cli) -> None:
        code, out, err = run_cli(["台北市信義區市府路1號"])

        assert code == 0
        assert out == "110204\n"
        assert err == ""

    def test_加上_a_參數會一併輸出標準化地址(self, run_cli) -> None:
        code, out, _ = run_cli(["-a", "台北市信義區市府路1號"])

        assert code == 0
        assert out == "110204\t臺北市信義區市府路1號\n"

    def test_查無六碼時降級輸出五碼並在_stderr_警告(self, run_cli) -> None:
        code, out, err = run_cli(["臺北市大安區羅斯福路四段1號"])

        assert code == 0  # 非嚴格模式視為成功
        assert out == "10617\n"
        assert "警告" in err
        assert "3+3" in err

    def test_嚴格模式下缺六碼離開碼為一(self, run_cli) -> None:
        code, out, err = run_cli(["--strict", "臺北市大安區羅斯福路四段1號"])

        assert code == 1
        assert out == "10617\n"
        assert "錯誤" in err

    def test_查無此地址時不輸出到_stdout(self, run_cli) -> None:
        code, out, err = run_cli(["不存在的亂碼地址xyz"])

        assert code == 1
        assert out == ""
        assert "查無此地址" in err

    def test_quiet_參數會壓掉警告(self, run_cli) -> None:
        code, _, err = run_cli(["-q", "不存在的亂碼地址xyz"])

        assert code == 1
        assert err == ""


class TestMultipleAddresses:
    """多筆地址查詢。"""

    def test_依輸入順序逐行輸出(self, run_cli) -> None:
        code, out, _ = run_cli(
            ["台北市信義區市府路1號", "臺北市大安區羅斯福路四段1號"]
        )

        assert code == 0
        assert out == "110204\n10617\n"

    def test_其中一筆失敗時離開碼為一但其餘照常輸出(self, run_cli) -> None:
        code, out, err = run_cli(["台北市信義區市府路1號", "不存在的亂碼地址xyz"])

        assert code == 1
        assert out == "110204\n"
        assert "查無此地址" in err


class TestFileAndStdin:
    """從檔案與標準輸入取得地址。"""

    def test_從檔案讀取地址(self, run_cli, tmp_path) -> None:
        path = tmp_path / "addresses.txt"
        path.write_text(
            "# 這行是註解\n"
            "台北市信義區市府路1號\n"
            "\n"
            "  臺北市大安區羅斯福路四段1號  \n",
            encoding="utf-8",
        )

        code, out, _ = run_cli(["-f", str(path)])

        assert code == 0
        assert out == "110204\n10617\n"

    def test_檔案不存在時回報錯誤(self, run_cli, tmp_path) -> None:
        code, out, err = run_cli(["-f", str(tmp_path / "沒有這個檔.txt")])

        assert code == 1
        assert out == ""
        assert "無法讀取地址來源" in err

    def test_以連字號從標準輸入讀取(self, run_cli) -> None:
        code, out, _ = run_cli(["-f", "-"], stdin_text="台北市信義區市府路1號\n")

        assert code == 0
        assert out == "110204\n"

    def test_管線輸入在沒有參數時自動生效(self, run_cli) -> None:
        code, out, _ = run_cli([], stdin_text="台北市信義區市府路1號\n")

        assert code == 0
        assert out == "110204\n"

    def test_命令列地址與檔案地址會合併(self, run_cli, tmp_path) -> None:
        path = tmp_path / "more.txt"
        path.write_text("臺北市大安區羅斯福路四段1號\n", encoding="utf-8")

        _, out, _ = run_cli(["台北市信義區市府路1號", "-f", str(path)])

        assert out == "110204\n10617\n"


class TestJsonMode:
    """JSON 輸出模式。"""

    def test_輸出合法_JSON_陣列(self, run_cli) -> None:
        code, out, _ = run_cli(
            ["--json", "台北市信義區市府路1號", "不存在的亂碼地址xyz"]
        )

        data = json.loads(out)
        assert code == 1
        assert len(data) == 2
        assert data[0] == {
            "ok": True,
            "address": "台北市信義區市府路1號",
            "zipcode6": "110204",
            "zipcode5": "11008",
            "normalized_address": "臺北市信義區市府路1號",
            "dataver6": "11505",
            "dataver5": "11208",
        }
        assert data[1]["ok"] is False

    def test_JSON_模式下_stdout_不摻雜警告(self, run_cli) -> None:
        _, out, err = run_cli(["--json", "臺北市大安區羅斯福路四段1號"])

        json.loads(out)  # 能解析代表沒有被警告污染
        assert "警告" in err


class TestInteractiveMode:
    """互動模式。"""

    def test_逐行查詢直到_EOF(self, run_cli) -> None:
        code, out, err = run_cli(
            [],
            stdin_text="台北市信義區市府路1號\n臺北市大安區羅斯福路四段1號\n",
            tty=True,
        )

        assert code == 0
        assert out == "110204\n10617\n"
        assert "郵遞區號小幫手" in err

    def test_輸入_q_可離開(self, run_cli) -> None:
        code, out, _ = run_cli(
            [], stdin_text="台北市信義區市府路1號\nq\n台北市信義區市府路1號\n", tty=True
        )

        assert code == 0
        assert out == "110204\n"  # q 之後的地址不會被查詢

    def test_空白行會被略過(self, run_cli) -> None:
        _, out, _ = run_cli([], stdin_text="\n\n台北市信義區市府路1號\n", tty=True)

        assert out == "110204\n"


class TestNetworkErrors:
    """網路異常情境。"""

    def test_連線失敗時回報錯誤並以離開碼一結束(self, run_cli) -> None:
        def broken(url: str, timeout: float, user_agent: str) -> bytes:
            raise urllib.error.URLError("Name or service not known")

        code, out, err = run_cli(["台北市信義區市府路1號"], fetch=broken)

        assert code == 1
        assert out == ""
        assert "無法連線" in err

    def test_使用者按下_Ctrl_C_會優雅結束(self, run_cli) -> None:
        def interrupted(url: str, timeout: float, user_agent: str) -> bytes:
            raise KeyboardInterrupt

        code, _, err = run_cli(["台北市信義區市府路1號"], fetch=interrupted)

        assert code == 130
        assert "已中斷查詢" in err

    def test_逾時會提示稍後再試(self, run_cli) -> None:
        def slow(url: str, timeout: float, user_agent: str) -> bytes:
            raise TimeoutError("timed out")

        code, _, err = run_cli(["台北市信義區市府路1號"], fetch=slow)

        assert code == 1
        assert "逾時" in err


class TestOptionsPassing:
    """參數是否確實傳遞到查詢層。"""

    def test_timeout_參數會傳入抓取函式(self, run_cli) -> None:
        seen: list[float] = []

        def spy(url: str, timeout: float, user_agent: str) -> bytes:
            seen.append(timeout)
            return encode(PAYLOAD_FULL)

        run_cli(["-t", "3.5", "台北市信義區市府路1號"], fetch=spy)

        assert seen == [3.5]

    def test_base_url_參數會改變查詢端點(self, run_cli) -> None:
        fetcher = FakeFetcher()

        run_cli(
            ["--base-url", "https://fake.local/api", "台北市信義區市府路1號"],
            fetch=fetcher,
        )

        assert fetcher.calls[0].startswith("https://fake.local/api?adrs=")


class TestPureHelpers:
    """CLI 內的純函式。"""

    def test_清理地址行(self) -> None:
        lines = ["  台北市  ", "", "# 註解", "新北市", "   "]

        assert clean_address_lines(lines) == ["台北市", "新北市"]

    def test_全部成功時離開碼為零(self) -> None:
        outcomes = [Success("台北市", "110204", "11008", "台北市")]

        assert decide_exit_code(outcomes, strict=False) == 0

    def test_有失敗時離開碼為一(self) -> None:
        outcomes = [
            Success("台北市", "110204", "11008", "台北市"),
            Failure("亂碼", FailureKind.NOT_FOUND, "查無"),
        ]

        assert decide_exit_code(outcomes, strict=False) == 1

    def test_嚴格模式對缺六碼較敏感(self) -> None:
        outcomes = [Success("台北市", "", "10617", "台北市")]

        assert decide_exit_code(outcomes, strict=False) == 0
        assert decide_exit_code(outcomes, strict=True) == 1

    def test_空結果視為成功(self) -> None:
        assert decide_exit_code([], strict=True) == 0
