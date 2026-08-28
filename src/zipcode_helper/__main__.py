"""支援 ``python -m zipcode_helper`` 的執行進入點。"""

from zipcode_helper.cli import entrypoint

if __name__ == "__main__":  # pragma: no cover - 由端對端測試以子行程驗證
    entrypoint()
