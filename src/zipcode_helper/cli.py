"""命令列介面。

負責:解析參數 → 取得地址來源 → 呼叫查詢層 → 輸出結果 → 決定離開碼。
所有純粹的邏輯(地址來源整理、離開碼判定)都拆成獨立函式以利測試。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import TextIO

from zipcode_helper import __version__
from zipcode_helper.api import FetchFn, fetch_raw, query_address, query_addresses
from zipcode_helper.formatter import (
    format_json,
    format_warning,
    format_zipcode_line,
)
from zipcode_helper.models import (
    DEFAULT_BASE_URL,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    Failure,
    Outcome,
    QueryConfig,
    Success,
)

logger = logging.getLogger("zipcode_helper")

EXIT_OK = 0
EXIT_HAS_PROBLEM = 1

#: 互動模式中代表「結束」的輸入
QUIT_WORDS = frozenset({"q", "quit", "exit", ":q", "離開", "結束"})


# --------------------------------------------------------------------------
# 純函式:輸入整理與離開碼判定
# --------------------------------------------------------------------------


def clean_address_lines(lines: Iterable[str]) -> list[str]:
    """整理來自檔案或標準輸入的地址清單。

    會去除前後空白,並略過空行與 ``#`` 開頭的註解行。

    Args:
        lines: 原始文字行。

    Returns:
        整理後的地址清單。
    """
    cleaned = (line.strip() for line in lines)
    return [line for line in cleaned if line and not line.startswith("#")]


def decide_exit_code(outcomes: Sequence[Outcome], strict: bool) -> int:
    """依查詢結果決定行程離開碼。

    Args:
        outcomes: 全部查詢結果。
        strict: 嚴格模式下,查不到六碼也算問題。

    Returns:
        全部順利回傳 ``0``;有任何失敗或(嚴格模式下)缺少六碼回傳 ``1``。
    """
    for outcome in outcomes:
        if isinstance(outcome, Failure):
            return EXIT_HAS_PROBLEM
        if strict and not outcome.has_six_digits:
            return EXIT_HAS_PROBLEM
    return EXIT_OK


def build_config(args: argparse.Namespace) -> QueryConfig:
    """把命令列參數轉換成查詢設定。

    Args:
        args: 已解析的參數。

    Returns:
        對應的 :class:`QueryConfig`。
    """
    return QueryConfig(
        base_url=args.base_url,
        timeout=args.timeout,
        delay=args.delay,
    )


# --------------------------------------------------------------------------
# 參數定義
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """建立命令列參數剖析器。"""
    parser = argparse.ArgumentParser(
        prog="zipcode",
        description="台灣郵遞區號小幫手:輸入地址,輸出 3+3 六碼郵遞區號。",
        epilog=(
            "範例:\n"
            "  zipcode 台北市信義區市府路1號\n"
            "  zipcode -a 台北市信義區市府路1號 新北市板橋區中山路一段161號\n"
            "  zipcode -f addresses.txt --json\n"
            "  cat addresses.txt | zipcode -f -\n"
            "  zipcode            # 不帶參數即進入互動模式\n\n"
            "資料來源:https://zip5.5432.tw(請勿短時間大量查詢,一天上限約 2000 次)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "addresses",
        nargs="*",
        metavar="地址",
        help="要查詢的台灣地址,可一次給多個",
    )
    parser.add_argument(
        "-f",
        "--file",
        metavar="檔案",
        help="從檔案讀取地址(每行一個,`-` 代表標準輸入)",
    )
    parser.add_argument(
        "-a",
        "--with-address",
        action="store_true",
        help="輸出郵遞區號時一併顯示標準化後的地址(以 Tab 分隔)",
    )
    parser.add_argument(
        "-j",
        "--json",
        dest="as_json",
        action="store_true",
        help="以 JSON 格式輸出完整結果",
    )
    parser.add_argument(
        "-s",
        "--strict",
        action="store_true",
        help="嚴格模式:查不到六碼即視為失敗(離開碼 1)",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        metavar="秒",
        help=f"批次查詢的間隔秒數(預設 {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="秒",
        help=f"單次查詢逾時秒數(預設 {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=argparse.SUPPRESS,  # 進階/測試用途,不列在說明中
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="不輸出警告訊息",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="顯示詳細日誌(-vv 更詳細)",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def configure_logging(verbosity: int) -> None:
    """依 ``-v`` 出現次數設定日誌層級。

    Args:
        verbosity: ``-v`` 的出現次數。
    """
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


# --------------------------------------------------------------------------
# 地址來源
# --------------------------------------------------------------------------


def read_addresses_from_file(path: str, stdin: TextIO) -> list[str]:
    """從檔案或標準輸入讀取地址清單。

    Args:
        path: 檔案路徑,``-`` 代表標準輸入。
        stdin: 標準輸入串流。

    Returns:
        整理後的地址清單。

    Raises:
        OSError: 檔案不存在或無法讀取。
    """
    if path == "-":
        logger.debug("從標準輸入讀取地址")
        return clean_address_lines(stdin)

    logger.debug("從檔案讀取地址:%s", path)
    content = Path(path).read_text(encoding="utf-8")
    return clean_address_lines(content.splitlines())


def collect_addresses(
    args: argparse.Namespace,
    stdin: TextIO,
) -> list[str]:
    """彙整所有來源的地址(命令列參數 + 檔案/標準輸入)。

    Args:
        args: 已解析的參數。
        stdin: 標準輸入串流。

    Returns:
        依序排列的地址清單。

    Raises:
        OSError: 指定的檔案無法讀取。
    """
    addresses = list(args.addresses)

    if args.file:
        addresses.extend(read_addresses_from_file(args.file, stdin))
    elif not addresses and not stdin.isatty():
        # 沒有任何參數但有人用管線餵資料進來,就把 stdin 當地址清單
        logger.debug("偵測到管線輸入,改由標準輸入讀取地址")
        addresses.extend(clean_address_lines(stdin))

    return addresses


# --------------------------------------------------------------------------
# 輸出
# --------------------------------------------------------------------------


def emit(
    outcome: Outcome,
    args: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    """輸出單筆結果:郵遞區號進 stdout,提醒訊息進 stderr。

    Args:
        outcome: 單筆查詢結果。
        args: 已解析的參數。
        stdout: 標準輸出串流。
        stderr: 標準錯誤串流。
    """
    warning = format_warning(outcome, args.strict)
    if warning and not args.quiet:
        print(warning, file=stderr)

    if isinstance(outcome, Success):
        print(format_zipcode_line(outcome, args.with_address), file=stdout)


def run_batch(
    addresses: Sequence[str],
    args: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
    fetch: FetchFn,
) -> int:
    """執行批次查詢並輸出結果。

    Args:
        addresses: 地址清單。
        args: 已解析的參數。
        stdout: 標準輸出串流。
        stderr: 標準錯誤串流。
        fetch: 抓取函式(測試可注入假實作)。

    Returns:
        本次執行的離開碼。
    """
    config = build_config(args)
    logger.info("開始查詢 %d 筆地址", len(addresses))

    results = query_addresses(addresses, config, fetch=fetch)

    if args.as_json:
        # JSON 模式必須先蒐集完整結果才能輸出合法陣列
        outcomes = list(results)
        for outcome in outcomes:
            warning = format_warning(outcome, args.strict)
            if warning and not args.quiet:
                print(warning, file=stderr)
        print(format_json(outcomes), file=stdout)
    else:
        outcomes = []
        for outcome in results:
            emit(outcome, args, stdout, stderr)
            outcomes.append(outcome)

    return decide_exit_code(outcomes, args.strict)


def run_interactive(
    args: argparse.Namespace,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    fetch: FetchFn,
) -> int:
    """互動模式:反覆讀入地址並即時查詢。

    Args:
        args: 已解析的參數。
        stdin: 標準輸入串流。
        stdout: 標準輸出串流。
        stderr: 標準錯誤串流。
        fetch: 抓取函式(測試可注入假實作)。

    Returns:
        離開碼,正常結束一律為 ``0``。
    """
    config = build_config(args)
    print("台灣郵遞區號小幫手(輸入地址後按 Enter;輸入 q 離開)", file=stderr)

    while True:
        print("地址> ", end="", file=stderr, flush=True)
        line = stdin.readline()

        if not line:  # 讀到 EOF(Ctrl-D)
            print(file=stderr)
            return EXIT_OK

        address = line.strip()
        if not address:
            continue
        if address.lower() in QUIT_WORDS:
            return EXIT_OK

        outcome = query_address(address, config, fetch=fetch)
        emit(outcome, args, stdout, stderr)


# --------------------------------------------------------------------------
# 進入點
# --------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    fetch: FetchFn = fetch_raw,
) -> int:
    """CLI 進入點。

    Args:
        argv: 命令列參數(不含程式名),預設取 ``sys.argv[1:]``。
        stdin: 標準輸入串流,預設 ``sys.stdin``。
        stdout: 標準輸出串流,預設 ``sys.stdout``。
        stderr: 標準錯誤串流,預設 ``sys.stderr``。
        fetch: 抓取函式,預設為真正的 HTTP 請求。

    Returns:
        離開碼:``0`` 全部順利、``1`` 有查不到或發生錯誤。
    """
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr

    try:
        addresses = collect_addresses(args, in_stream)
    except OSError as exc:
        print(f"錯誤:無法讀取地址來源 — {exc}", file=err_stream)
        return EXIT_HAS_PROBLEM

    try:
        if addresses:
            return run_batch(addresses, args, out_stream, err_stream, fetch)
        return run_interactive(args, in_stream, out_stream, err_stream, fetch)
    except KeyboardInterrupt:
        print("\n已中斷查詢", file=err_stream)
        return 130


def entrypoint() -> None:  # pragma: no cover - 由 console script 呼叫
    """供 ``python -m`` 與 console script 使用的包裝函式。"""
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
