# Session DB / API Correlation Handoff

## 1. 狀態

本輪目標是先修好 DB，再讓 API call 自身耗時可以獨立排出，並能用同一條 correlation 對帳 `API → tool → DB`。目前 **DB/API slice 已完成 source、focused integration 與 production runtime 驗證；SQLite/storage 底層 root cause 尚未定案**。

Gateway 已由使用者重開，PID `1175532` 已透過真實 agent/tool request 驗證本輪 source 的 runtime 行為；本輪不再重啟 gateway。

## 2. 鏈路狀態

`Discord inbound → turn`：**PARTIAL**。既有 turn telemetry 存在，但本輪沒有重新封閉所有 inbound path。

`turn → provider API call`：**PARTIAL / source verified**。既有 `API_CALL_START`、`API_CALL_RESPONSE`、`API_CALL_ERROR` 會使用 `turn_id` 與 `api_request_id`；本輪沒有宣稱所有 retry、fallback、timeout、cancel path 都已 production 對帳。

`API call → tool`：**SOURCE + focused test verified**。tool execution 會把 `turn_id`、`api_request_id`、`tool_call_id` 綁入 lifecycle ContextVar；既有 tool lifecycle tests 綠燈。

`tool → session_search → DB`：**SOURCE + temporary DB integration verified**。`session_search` 不新增模型可填的 argument，而是讀上游 lifecycle context，再將 scalar correlation metadata 綁入 DB telemetry context。

`DB operation → lock/query/transaction`：**FOCUSED TEST VERIFIED**。既有 DB lifecycle、lock owner/waiter、query timing、transaction 與 payload-free error telemetry 保留；本輪新增 upstream ID assertions。

`SQLite/WAL/filesystem → machine health`：**PARTIAL / production overlay verified**。兩筆 production request 已與同 boot ID、同時段 machine-health 對齊；machine record 仍沒有 request-level correlation，且 overlay 不足以指定 storage/fsync/IOPS 根因。

`tool result → stream → Discord delivery`：**PARTIAL**。屬既有上層 lifecycle 工作，本輪不擴張範圍。

## 3. 本輪最小改動

`agent/lifecycle_telemetry.py`：新增 payload-free `ContextVar` context helper，提供 `lifecycle_context()` 與 `get_lifecycle_context()`。

`agent/tool_executor.py`：在 concurrent `_invoke_tool` 與 sequential `session_search` middleware path 綁定 `trace_id`、`turn_id`、`api_request_id`、`tool_call_id`、session/task metadata。沒有把這些欄位加入 tool schema。

`tools/session_search_tool.py`：新增 wrapper，將上游 lifecycle IDs 傳入 `hermes_state.db_telemetry_context()`，覆蓋整個 session-search DB path。

`hermes_state.py`：新增 DB upstream context binding；DB event 與 lock event 顯示 `turn_id`、`api_request_id`、`tool_call_id`、`session_id`。原本的 `request_id` 仍是 session-search discovery/request correlation ID，兩者語意不混用。

`tests/test_session_db_lock_telemetry.py`：新增真實 temporary DB path test，驗證 `session_search()` 產生的 DB operation events 可帶回上游 API/tool IDs。

## 4. 證據

Source：`py_compile` 通過：

```text
agent/lifecycle_telemetry.py
agent/tool_executor.py
tools/session_search_tool.py
hermes_state.py
tests/test_session_db_lock_telemetry.py
```

DB focused suite：

```text
7 tests passed, 0 failed
```

其中新增 contract test 的獨立結果：

```text
1 passed in 0.52s
```

既有上層 lifecycle/tool focused suite：

```text
4 tests passed, 0 failed
```

`git diff --check`：通過。

## 5. Correlation contract

上游 lifecycle context 使用：`trace_id = turn_id`、`api_request_id = logical API call ID`、`tool_call_id = provider tool call ID`。

API timing 的 source boundary 已確認：`agent/conversation_loop.py` 在 retry loop 前設定 `api_start_time` 並發出 `API_CALL_START`；`API_CALL_RESPONSE` 在 `run_llm_execution_middleware(...)` 返回後計算 `duration_ms`。所以這是一次 logical API call 的 aggregate duration，可能包含 middleware、retry/fallback 與 transport execution；不是 provider-only latency。錯誤路徑使用同一個 start boundary，但 runtime 仍需補齊各種 timeout/cancel/fallback 對帳。

DB context 使用同一個 `trace_id`，DB operation 自己產生 `db_operation_id/span_id`，其 `parent_span_id` 指向 tool span；DB event 同時保留 `turn_id`、`api_request_id`、`tool_call_id`、`session_id`，而 session-search 自己的 `request_id` 用來串 discovery 底層 phases。

因此 production log 預期可以查：

```text
API_CALL_START
→ TOOL_START
→ DB_OPERATION_START
→ DB_QUERY_* / DB_LOCK_*
→ DB_OPERATION_END
→ TOOL_END
→ API_CALL_RESPONSE 或 API_CALL_ERROR
```

這是 source/integration contract；本輪也已沿真實 agent/tool path 在 production runtime 出現，底層 attribution 仍未完成。

## 6. 未完成與禁止事項

尚未完成：所有 API retry/fallback/timeout/cancel terminal path 的 production runtime audit、machine-health 的 request-level correlation、以及 `ctx_sql_ms` 內部 execute/first-row/fetch-batch/thread-CPU attribution。

本輪不要做：再次重啟 gateway、重開 VM、修改 cron、修改 SQLite durability、修改 schema、做 destructive cleanup、reset working tree、commit。

既有 full regression 的限制仍保留：完整 `tests/test_hermes_state.py` 曾在 300 秒被 timeout；部分 hermetic fixture 會缺 `simple` tokenizer。focused green 不等於完整 regression green。

## 7. Production runtime verification：2026-07-18 20:38–20:41 +08

### Fact：目前 gateway 已載入本輪 source 的可觀測行為

Gateway Python process 是 PID `1175532`，`2026-07-18 20:24:41 +08` 啟動。`hermes_state.py`、`agent/lifecycle_telemetry.py`、`agent/tool_executor.py`、`tools/session_search_tool.py` 的 mtime 都早於該啟動時間。這證明 process 是在本輪 source 修改後啟動；但沒有直接 attach process 讀 `sys.modules`，所以「exact import path」仍以 emitted runtime fields 交叉確認。

### Fact：API → tool → DB correlation 已在 production path 出現

本次真實 tool call 的 correlation：

```text
trace_id:
  20260716_123238_6cd9cfbe:20260716_123238_6cd9cfbe:653b0b75
api_request_id:
  ...:api:6
tool_call_id:
  call_JKrXGD3sMJyOYaoV3wPuAqnX
session-search request_id:
  123021ca4d45
```

事件鏈實際出現：

```text
API_CALL_START       duration boundary starts
API_CALL_RESPONSE    2,573 ms
TOOL_START           session_search
DB_OPERATION_START  search_messages
DB_QUERY_*           search/context query lifecycle
DB_OPERATION_END    search_messages
GET_MESSAGES_AROUND
ANCHORED_VIEW
DISCOVER_DONE
TOOL_END            142,784 ms
```

`DB_OPERATION_*` 與 `DB_QUERY_*` 已帶回 `turn_id`、`api_request_id`、`tool_call_id`、`session_id`、`request_id`。這是 production runtime evidence，不再只是 temporary DB test。

### Fact：這一筆慢在哪裡

```text
API_CALL_RESPONSE duration_ms       2,573
TOOL_END duration_ms               142,784
DISCOVER_DONE total_ms             142,783
SEARCH_PHASE search_total_ms       141,766
SEARCH_PHASE fts_execute_ms         16,620
SEARCH_PHASE ctx_sql_ms            124,539
SEARCH_PHASE ctx_decode_ms              25
SEARCH_PHASE ctx_build_ms             129
SEARCH_PHASE view_ms                  628
SEARCH_PHASE raw_hits                  74
SEARCH_PHASE unique_sessions            3
DISCOVER_DONE results                 2
DISCOVER_DONE unique_lineages          2
context_rows_loaded                 8,866
context_bytes_loaded           22,240,655
```

`SEARCH_CONTEXT_SESSION` 顯示三個 batch target，其中主要 session 有 `72` raw hits、`8,564` rows、`20,446,078` bytes；另外兩個 session 分別只有 `263` rows / `1,774,615` bytes 與 `39` rows / `19,962` bytes。這裡的 `query_ms=124,539` 是 batch SQL duration，不能拿來當每個 session 的獨立耗時。

### Fact：這一筆不是自己在等 application lock

同一個 `session_db_python_lock` scope 下，`search_messages` 的 lock：

```text
FTS lock hold       16,657 ms
context lock hold  124,539 ms
search lock wait         0 ms
```

同一時間窗 `20:38:20–20:40:50` 沒有 `DB_LOCK_WAIT_SLOW`、`database is locked` 或 writer waiter event 指向這個 lock。search 結束後約 `20:40:45.555` 才有一筆 write 開始，該 write hold `786 ms`，時間上在 search release 之後。

所以這一筆可以確定：search 是長時間的 application-lock owner；不能確定它是被其他 writer 拉慢，也不能把本筆 latency 報成 lock-wait convoy。

### Fact：machine-health 是同時段 overlay，不是 request-level correlation

同一個 boot ID `29a7a120-82dd-46ad-841e-cc925536f73d` 的 machine sample：

```text
local wall time       2026-07-18 20:39:09 +08
iowait_percent        54.77
busy_percent           4.02
load_1m                2.4126
memory_available       278,220,800 bytes
root weighted_io_ms    2,517
root reads             115
root writes              0
state_db_size          2,385,010,688 bytes
state_db_wal_size         2,410,232 bytes
```

該 machine record 的 correlation 欄位都是 null。它只能證明同時段 VM 有高 iowait overlay，不能證明 `ctx_sql_ms=124,539` 是 storage、fsync 或 IOPS 造成。

### Inference

這筆 production sample 支持：

```text
session_search 的主要成本在 context SQL / fetch boundary；
FTS 是第二個明顯成本；view、decode、build、JSON 不是主因。
```

它也支持「search 持鎖時間會讓其他 DB 操作有被阻塞的可能」，因為持鎖區段超過兩分鐘；但本筆時間窗沒有 waiter edge，所以不能把「可能阻塞別人」升級成「本筆已造成 writer latency」。

### Hypothesis，仍未定案

候選包括 SQLite execution / `sqlite3_step()`、cursor materialization、OS page cache / filesystem latency、scheduler / VM iowait，以及 context batch query 的 SQL shape。下一個必要 probe 是把 `ctx_sql_ms` 再拆成 execute、first-row、fetch-batch wall time 與 thread CPU time 的 production sample；目前 `ctx_fetch_ms=0` 不足以證明沒有 fetch 成本，它反映的是目前 timing boundary。

目前仍不得宣稱 SQLite、storage、fsync、IOPS、scheduler 或 provider 是 root cause。

## 8. 第二筆 production sample

### Fact：第二筆仍由同一條 context path 主導

第二筆真實 tool call 使用另一組 API/tool span，但同一個 turn trace：

```text
api_request_id: ...:api:22
tool_call_id:   call_66ZCge0Ipw5A1RRzQquo0VHU
request_id:     93df1c1bfca7
```

實測：

```text
API_CALL_RESPONSE duration_ms       11,926
TOOL_END duration_ms               233,089
DISCOVER_DONE total_ms             233,006
SEARCH_PHASE search_total_ms       210,397
SEARCH_PHASE fts_execute_ms         32,635
SEARCH_PHASE ctx_sql_ms            175,907
SEARCH_PHASE ctx_decode_ms              34
SEARCH_PHASE ctx_build_ms             292
SEARCH_PHASE view_ms               21,871
SEARCH_PHASE raw_hits                 202
unique_sessions                         4
context_rows_loaded                12,270
context_bytes_loaded           30,666,488
DISCOVER_DONE results                 3
DISCOVER_DONE unique_lineages          3
```

四個 target session 中，主要 session 有 `196` raw hits、`8,596` rows、`20,633,585` bytes；其餘 session 分別有 `263` rows、`1,945` rows、`1,466` rows。這筆的 `SEARCH_PHASE lock_wait_ms=0`，但 `lock_hold_ms=209,486`，其中 context lock hold 是 `175,907 ms`。

### Inference：不是 warm cache 解決問題，而是 workload/DB boundary 隨結果量變重

兩筆 production sample 都有相同形狀：`ctx_sql_ms` 是最大的單一區段，search 自己沒有 application-lock wait，卻持有 lock 很久。第二筆 raw hits 從 `74` 增到 `202`、context rows 從 `8,866` 增到 `12,270`、bytes 從 `22.2 MB` 增到 `30.7 MB`，而 context SQL 從 `124.539s` 增到 `175.907s`；這支持「context batch query / fetch boundary 的成本會隨工作量與當時 machine state 放大」。這仍不是 N+1、SQLite、storage 或 IOPS 的定案。

第二筆期間的 machine overlay：

```text
20:43:06  iowait 47.50%  load1 0.4561  memory_available 326,103,040
20:45:36  iowait 49.25%  load1 1.5166  memory_available 334,602,240
20:47:37  iowait 88.94%  load1 1.1230  memory_available 391,852,032
```

machine records 的 correlation 欄位仍全部是 null；它們可以和 request wall-clock 對齊，但不是 request-level snapshot。第二筆因此比第一筆更支持「context SQL 長時間與 VM iowait 同時出現」，仍不能把同時發生寫成因果。

不再重開 gateway。下一步只做 read-only source/SQL boundary analysis，並保留兩筆 production sample 的分布；不要先改 SQLite durability、lock architecture、cron 或 VM 設定。

若欄位缺失，先沿 import path 與 runtime process start time 查證，不要再增加第四套 telemetry。若事件鏈完整，再用 production timing 對帳 API、tool、DB operation、lock wait、query/fetch；沒有這筆證據前，不宣稱 SQLite、storage、fsync、iowait 或 scheduler 是 root cause。
