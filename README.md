# 郵遞區號小幫手(zipcode-helper)

輸入台灣中文地址,輸出 **3+3 六碼郵遞區號**的命令列工具。
資料來源為 [zip5.5432.tw](https://zip5.5432.tw) 提供的公開 API。

```console
$ zipcode 台北市信義區市府路1號
110204
```

## 特色

- 單筆、多筆、檔案、管線、互動模式都能查
- 預設只把郵遞區號輸出到 `stdout`,警告訊息走 `stderr`,可直接接管線使用
- 查不到六碼時自動降級為五碼並提出警告,`--strict` 可要求必須有六碼
- `--json` 輸出完整結果(六碼、五碼、標準化地址、郵局資料版號)
- 遵守 API 使用禮儀:批次查詢預設每筆間隔 2 秒
- 除了提供 CA 憑證的 `certifi` 之外,只使用 Python 標準函式庫

## 安裝

需要 Python 3.11 以上。建議使用 [uv](https://github.com/astral-sh/uv) 建立獨立環境:

```bash
git clone https://github.com/bonzewu/zip1.git
cd zip1
uv venv
uv pip install -e .
```

安裝後即可使用 `zipcode` 指令:

```bash
.venv/bin/zipcode 台北市信義區市府路1號
```

或不安裝、直接以模組方式執行:

```bash
PYTHONPATH=src python3 -m zipcode_helper 台北市信義區市府路1號
```

## 使用方式

### 單筆查詢

```console
$ zipcode 新北市板橋區中山路一段161號
220242
```

### 一併顯示標準化地址

```console
$ zipcode -a 新北市板橋區中山路一段161號
220242	新北市板橋區中山路１段161號
```

### 多筆查詢

```console
$ zipcode 台北市信義區市府路1號 新北市板橋區中山路一段161號
110204
220242
```

### 從檔案或管線批次查詢

檔案中每行一個地址,空行與 `#` 開頭的註解會自動略過:

```console
$ zipcode -f addresses.txt
$ cat addresses.txt | zipcode -f -
$ cat addresses.txt | zipcode          # 偵測到管線時自動讀取
```

### JSON 輸出

```console
$ zipcode --json 台北市信義區市府路1號
[
  {
    "ok": true,
    "address": "台北市信義區市府路1號",
    "zipcode6": "110204",
    "zipcode5": "11008",
    "normalized_address": "臺北市信義區市府路1號",
    "dataver6": "11505",
    "dataver5": "11208"
  }
]
```

### 互動模式

不帶任何參數執行即進入互動模式,輸入 `q` 或按 <kbd>Ctrl</kbd>+<kbd>D</kbd> 離開:

```console
$ zipcode
台灣郵遞區號小幫手(輸入地址後按 Enter;輸入 q 離開)
地址> 台北市信義區市府路1號
110204
地址> q
```

## 參數說明

| 參數 | 說明 |
| --- | --- |
| `地址 [地址 ...]` | 要查詢的台灣地址,可一次給多個 |
| `-f, --file 檔案` | 從檔案讀取地址(每行一個,`-` 代表標準輸入) |
| `-a, --with-address` | 輸出時一併顯示標準化後的地址(以 Tab 分隔) |
| `-j, --json` | 以 JSON 格式輸出完整結果 |
| `-s, --strict` | 嚴格模式:查不到六碼即視為失敗 |
| `-d, --delay 秒` | 批次查詢的間隔秒數(預設 `2.0`) |
| `-t, --timeout 秒` | 單次查詢逾時秒數(預設 `10.0`) |
| `-q, --quiet` | 不輸出警告訊息 |
| `-v, --verbose` | 顯示詳細日誌(`-vv` 更詳細) |
| `-V, --version` | 顯示版本 |

## 離開碼

| 離開碼 | 意義 |
| --- | --- |
| `0` | 全部查詢皆成功 |
| `1` | 有地址查不到、發生網路錯誤,或嚴格模式下缺少六碼 |
| `2` | 命令列參數用法錯誤 |

適合用在腳本中:

```bash
if zipcode --strict --quiet "$ADDRESS" > /tmp/zip.txt; then
    echo "六碼郵遞區號:$(cat /tmp/zip.txt)"
else
    echo "查詢失敗" >&2
fi
```

## 關於 3+3 六碼的涵蓋範圍

中華郵政的 3+3 六碼資料涵蓋範圍小於 3+2 五碼,部分地址查得到五碼卻查不到六碼。
遇到這種情況時,本工具會:

- 在 `stdout` 輸出可用的五碼
- 在 `stderr` 印出警告,說明只查到五碼
- 離開碼仍為 `0`(若希望視為失敗,請加上 `--strict`)

```console
$ zipcode 臺北市大安區羅斯福路四段1號
警告:臺北市大安區羅斯福路四段1號 — 此地址查無 3+3 六碼郵遞區號(僅查得五碼 10617);郵局 3+3 資料涵蓋範圍小於 3+2,可嘗試補上完整門牌號碼
10617
```

補上完整門牌號碼(含巷弄、號)通常能提高查到六碼的機率。

## API 使用規範

本工具使用 [zip5.5432.tw](https://zip5.5432.tw/zip5api.html) 的公開 API,請遵守站方規範:

- 每天查詢不超過 **2000 次**
- 查詢之間保留 **2~3 秒**緩衝(本工具預設 `--delay 2.0`)
- 不要重複查詢相同地址
- 查詢結果僅供參考,**以中華郵政公告資料為準**

## 疑難排解

### 出現「HTTPS 憑證驗證失敗」

macOS 上以 python.org 安裝套件安裝的 Python,若沒有執行過憑證安裝腳本,
就找不到 CA 憑證。兩種解法擇一:

```bash
# 解法一:安裝 certifi(本專案已預設相依,通常安裝時就會處理)
uv pip install certifi

# 解法二:執行 Python 安裝目錄中的憑證安裝腳本
open "/Applications/Python 3.14/Install Certificates.command"
```

### 查詢一直逾時

站方要求查詢之間保留緩衝時間,短時間大量查詢可能被限制。
請確認沒有把 `--delay` 調到 0,並稍後再試。

## 開發與測試

```bash
uv venv
uv pip install -e '.[dev]'

# 單元測試 + 功能測試 + 本機端對端測試(不需外網)
.venv/bin/pytest

# 加測真實 API 的端對端測試(會實際連線 zip5.5432.tw)
.venv/bin/pytest --run-e2e

# 涵蓋率
.venv/bin/pytest --cov=zipcode_helper --cov-report=term-missing
```

測試分層:

| 檔案 | 層級 | 說明 |
| --- | --- | --- |
| `tests/test_unit_api.py` | 單元 | 網址組裝、回應解析、錯誤分類、批次節流 |
| `tests/test_unit_formatter.py` | 單元 | stdout / stderr / JSON 的輸出格式 |
| `tests/test_functional_cli.py` | 功能 | 注入假 API,走完整 CLI 流程與離開碼 |
| `tests/test_e2e_cli.py` | 端對端 | 子行程執行 CLI,對本機假伺服器與真實 API |

## 專案結構

```
src/zipcode_helper/
├── __init__.py      # 套件公開介面
├── __main__.py      # python -m zipcode_helper 進入點
├── models.py        # 不可變資料模型(Success / Failure / QueryConfig)
├── api.py           # API 存取層,純解析函式與網路 I/O 分離
├── formatter.py     # 呈現層,全部為純函式
└── cli.py           # 命令列介面與流程控制
```

設計上刻意把純函式(解析、格式化、離開碼判定)與副作用(HTTP 請求、列印、等待)分開,
因此絕大多數邏輯都能在不碰網路的情況下測試。

## 授權

MIT License
