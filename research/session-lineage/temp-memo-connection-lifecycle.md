# TEMP memo connection/lifecycle audit (#50)

> Research-only artifact for #50 / #45. This does **not** implement the TEMP-memo contender.
>
> Question: can a tiny query-local SQLite TEMP table safely provide the keyed `node -> resolved_root` state that ordinary SQLite recursive CTEs lack, under Hermes' **actual** connection, lock, transaction, and cleanup model?

## 0. Executive result

**TEMP memo is mechanically viable, but the safe seam is narrower than “CREATE TEMP TABLE around the current code”.**

The decisive findings are:

1. **Current `search_session_winners()` is not on `_read_ctx()`.** At pinned fork `dev`, the entire winner SELECT executes under `self._lock` on the shared writer `self._conn`. A direct multi-statement TEMP implementation there would be connection-safe and naturally serialized inside one `SessionDB`, but it would extend the same-process global writer-lock hold from one statement to the whole TEMP lifecycle.
2. **WAL `_read_ctx()` has exactly the connection shape TEMP wants.** It returns one persistent, per-thread `mode=ro` connection, `isolation_level=None`, with no `self._lock`; non-WAL or reader-open failure falls back to the locked writer connection. SQLite's TEMP schema is connection-local, so per-query TEMP state on that connection is isolated from other reader threads/connections.
3. **A read-only main database does not make the TEMP schema read-only.** A focused probe using `file:...?mode=ro` successfully created, populated, queried, and dropped a TEMP table while a main-schema write correctly failed with `attempt to write a readonly database`. This follows SQLite's architecture: TEMP objects live in a separate connection-local temp database.
4. **The hidden correctness requirement is snapshot preservation.** Today's winner selection is one SQL statement, therefore one main-database read snapshot. A TEMP memo algorithm necessarily uses multiple SQL statements. With Hermes' autocommit connections, those statements would otherwise be separate transactions and could observe different commits. The safe shape must hold one explicit **read transaction** across the candidate/traversal/winner phase.
5. **That read transaction is compatible with TEMP writes in WAL.** Probe: `BEGIN` on a `mode=ro` connection, read main DB, create/write TEMP memo, then a separate writer connection updated and committed the main DB while the reader stayed open; the reader continued to see its original snapshot until `COMMIT` and then saw the new value. TEMP writes did not turn the read-only main DB into a main writer.
6. **Per-query unique table (A) remains the Occam choice.** There is no concrete need for a reusable connection-local table plus `query_id` (B). Unique names + `try/finally DROP TABLE IF EXISTS` avoid cross-query collision, and connection close is a second cleanup boundary if the DROP cannot run.
7. **TEMP DDL itself is tiny in the local probe; statement count is the larger risk.** On the probe runtime, warm empty keyed `CREATE TEMP` + `DROP` was ~0.042 ms median / ~0.061 ms p95. `CREATE + 300 UPSERT rows + 300 indexed point lookups + DROP` was ~1.18 ms median / ~1.70 ms p95. These are **not production benchmark numbers**; they only show that lifecycle DDL is not obviously disqualifying. Benchmark-v2 still needs the deployment SQLite/runtime and the actual traversal statement shape.

**Disposition for #45:** keep TEMP memo in the benchmark/design space. Do not implement it by merely lengthening the current writer-locked block. The clean integration seam is one connection-owning winner phase that can use `_read_ctx()` in WAL, opens one explicit read transaction, creates one uniquely named TEMP memo, performs all memoized traversal on that same connection, commits/rolls back, and drops the table in guaranteed cleanup. Under non-WAL fallback the same helper remains correct but holds `self._lock` for the phase, so lock-duration telemetry is mandatory.

---

## 1. Pinned research receipt

Research date: 2026-08-09.

| Item | Immutable pin / state |
|---|---|
| fork `dev` at research start | `311bf7d6d28b204f0aa977ddcd05d44141d2d4ba` |
| #46 code-map PR | #49, head `916c5de2d3de905328f7a5f78432bf941485f147` |
| #47 prior-art PR | #48, head `186d50b4ec95b3ec429cf545dbd5f391bce081aa` |
| active decision ticket | #45 |
| focused lifecycle ticket | #50 |
| upstream read-path foundation | NousResearch/hermes-agent#73344, merged as `f228e145ba35cbbf785eded2021ae6682285b91b` and ancestor of pinned fork `dev` |

Pinned blobs used for the lifecycle map:

| path | blob |
|---|---|
| `tools/session_search_tool.py` | `24f4d077c3bda862ba6ca74d1f14000527f8f866` |
| `hermes_state_search.py` | `15daf505aad40017b0cc7c85c94ec928e8af6684` |
| `hermes_state.py` | `2710a54b139a75ec304051900c8e0820d18d1bb0` |

### Provenance labels

- **UPSTREAM-MERGED-IN-BASE** — accepted upstream behavior whose merge is an ancestor of pinned fork `dev`.
- **FORK-DEV** — current fork-only behavior after that base.
- **FORK-HISTORICAL** — old fork prototype/donor behavior, not current production.
- **UPSTREAM-OPEN** — unmerged upstream evidence only.

---

## 2. Exact call and connection ownership map

### 2.1 Tool layer

Pinned source: `tools/session_search_tool.py`.

| range / symbol | behavior relevant to #50 | connection consequence |
|---|---|---|
| `L692-L861` `_discover()` | resolve current lineage, run exact-title lane, call `db.search_session_winners(...)`, then hydrate each winner with `get_anchored_view()` and `get_session()` | winner selection and hydration are separate DB method calls; no outer DB context is held by `_discover()` |
| `L863-L985` `session_search()` | synchronous dispatcher; discovery ends by directly returning `_discover(...)` | there is no `await`, yield, or cooperative scheduling point inside the search call itself |
| `L706-L740` winner handoff | calls `search_session_winners(...)` exactly once for the DB lane | TEMP state only needs to survive the winner phase, not the whole JSON/hydration phase |
| `L773-L835` hydration | each returned winner calls `get_anchored_view()` and `get_session()` separately | TEMP state should be gone before hydration; carrying it longer buys nothing |

`session_search()` can construct its own `SessionDB()` when `db is None`, while normal injected callers may share an existing instance. Therefore correctness must be per-connection, not dependent on there being exactly one `SessionDB` in the process.

### 2.2 Current winner-selection seam

Pinned source: `hermes_state_search.py`.

- `L1307-L1720`: `search_session_winners()` builds candidate selection, generic-parent lineage resolution, dedupe, exclusions, and final winner LIMIT.
- `L1673-L1679`: the decisive execution block is:

```python
with self._lock:
    cursor = self._conn.execute(sql, sql_params)
    rows = [dict(row) for row in cursor.fetchall()]
```

This is **FORK-DEV** behavior. It is a single SQL statement on the shared writer connection.

Consequences:

- Within one `SessionDB`, simultaneous calls from different threads cannot interleave inside winner selection: `self._lock` serializes them.
- The Python lock is held until `fetchall()` completes.
- Because the whole winner pipeline is one SQLite statement, it naturally has one read snapshot.
- A naïve TEMP contender implemented as `CREATE -> many statements -> DROP` under the same `with self._lock:` would remain same-connection and collision-safe, but would extend this lock across every traversal statement.
- `self._lock` is process-local. It says nothing about a different Hermes process / different `SessionDB` connection touching the same main DB.

### 2.3 Writer and read connections

Pinned source: `hermes_state.py`.

#### Writer / explicit `read_only=True` construction — approximately `L1944-L2110`

`SessionDB.__init__` creates:

```text
self._lock = threading.Lock()
self._read_local = threading.local()
self._read_conns = set()
self._read_conns_lock = threading.Lock()
```

Normal writer connection:

```text
path = state.db
check_same_thread = False
timeout = 1.0
isolation_level = None
```

then WAL-with-fallback and configured PRAGMAs are applied.

Explicit `SessionDB(read_only=True)` connection:

```text
URI = file:<state.db>?mode=ro
uri = True
check_same_thread = False
timeout = 1.0
isolation_level = None
```

This connection is still one SQLite connection with its own TEMP schema; `mode=ro` applies to the named main database file, not to an independently created TEMP database.

#### WAL reader — `L2197-L2267` `_get_read_conn()` / `_read_ctx()`

`_get_read_conn()`:

- only supplies a separate reader when `self._wal_active` and not `self.read_only`;
- caches it in `threading.local()`;
- opens `file:<state.db>?mode=ro`, `uri=True`, `timeout=5.0`, `isolation_level=None`;
- does **not** set `check_same_thread=False`, so Python's sqlite3 default thread affinity stays in force; this is coherent with storing it in thread-local state;
- loads the tokenizer extensions needed by FTS on that connection;
- registers the connection in `_read_conns` for deterministic shutdown cleanup.

`_read_ctx()`:

```text
WAL + reader available:
    yield per-thread mode=ro reader
    NO self._lock

non-WAL / read-open failure / read_only SessionDB:
    with self._lock:
        yield self._conn
```

This abstraction is **UPSTREAM-MERGED-IN-BASE**. Upstream PR #73344 merged the read-path split, and its merge commit is an ancestor of pinned fork `dev`.

#### Read-connection cleanup — approximately `L2801-L2845` `close()`

`SessionDB.close()`:

1. marks `_read_conns_closed` under `_read_conns_lock`;
2. drains and closes every tracked per-thread read connection;
3. clears thread-local current connection;
4. under `self._lock`, closes `self._conn` (with writable WAL checkpoint behavior where applicable).

That gives TEMP memo a hard second cleanup boundary: anything left in a connection's TEMP schema disappears when that connection closes.

### 2.4 Winner selection versus hydration: same connection?

**Today, normally no in WAL mode.**

- `search_session_winners()` explicitly uses writer `self._conn` under `self._lock`.
- `get_anchored_view()` is part of the upstream read-path split; its message/bookend reads use `_read_ctx()`.
- `get_session()` is likewise in the upstream #73344 read-path set.

So a normal WAL discovery looks like:

```text
_discover
  |
  | current/title helper reads ----------> per-thread read conn (via read methods)
  |
  | search_session_winners --------------> writer self._conn + self._lock
  |
  ` winner hydration --------------------> per-thread read conn
```

In non-WAL fallback, `_read_ctx()` itself falls back to `self._conn` under `self._lock`, so the physical connection may be the same across phases, but the lock is acquired separately per helper call; there is still no outer connection lifetime spanning `_discover()`.

**TEMP state therefore belongs strictly inside the optimized winner-selection helper.** Hydration must not depend on it.

---

## 3. Can two searches interleave on one connection?

The useful answer needs to separate **logical searches**, **Python locks**, **SQLite connections**, and **SQLite transactions**.

### Timeline A — current shared `SessionDB`, current winner path

```text
Thread A                         shared SessionDB                     Thread B
   |                                   |                                |
   | search_session_winners()          |                                |
   |---- acquire self._lock ---------->|                                |
   |---- one SELECT on self._conn ---->|                                |
   |---- fetchall --------------------->|                                |
   |                                   |<---- waits for self._lock ------|
   |---- release ---------------------->|                                |
   |                                   |<---- acquire -------------------|
   |                                   |<---- B SELECT/fetchall ---------|
```

**Actual shape:** serialized. No statement-level interleaving on `self._conn` inside winner selection.

### Timeline B — future winner phase on WAL `_read_ctx()`

```text
Thread A                              Thread B
   |                                     |
   | _read_ctx -> reader A               | _read_ctx -> reader B
   | BEGIN snapshot A                    | BEGIN snapshot B
   | TEMP memo A                         | TEMP memo B
   | traversal statements                | traversal statements
   | COMMIT                              | COMMIT
   | DROP TEMP A                         | DROP TEMP B
```

Each thread owns a different connection and therefore a different TEMP schema. They can overlap without table-name interaction even if the generated names accidentally match; unique names are still preferred for stale-cleanup/reentrancy defense and debugging clarity.

### Timeline C — same thread, per-thread reader

`session_search()` / `_discover()` / `search_session_winners()` are synchronous Python functions with no cooperative yield point. In the normal call path, two logical searches cannot interleave statement-by-statement on the same thread: call A returns before call B begins.

A same-thread connection can nevertheless be reused sequentially for many searches because `_get_read_conn()` caches it in `threading.local()`. That is why cleanup cannot rely on “the connection will close after each search”.

### Timeline D — separate `SessionDB` instances

Two independent `SessionDB` objects have different Python locks and different SQLite connections even if they target the same `state.db`. Their operations can overlap at SQLite/VFS level. TEMP schema remains connection-local, so a TEMP table in one object is invisible to the other.

### What is *not* a lock guarantee

`self._lock` only serializes users of that specific Python `SessionDB` instance. It does not serialize:

- another process;
- another `SessionDB` object;
- the WAL reader connections returned by `_read_ctx()`.

Therefore a multi-statement contender cannot use the Python lock as a substitute for a database snapshot contract.

---

## 4. TEMP semantics from SQLite primary documentation

Primary references:

- TEMP database and lifecycle: https://www.sqlite.org/tempfiles.html#temp_databases
- `CREATE TEMP TABLE`: https://www.sqlite.org/lang_createtable.html
- URI `mode=ro`: https://www.sqlite.org/uri.html
- transactions: https://www.sqlite.org/lang_transaction.html
- `PRAGMA temp_store`: https://www.sqlite.org/pragma.html#pragma_temp_store
- schema-change/reprepare behavior: https://www.sqlite.org/rescode.html#schema

### 4.1 Visibility and lifetime

SQLite documents that TEMP tables are visible only to the connection that created them. TEMP tables/indices/triggers/views live in a separate temporary database associated with that connection, and the temp database is automatically deleted when the connection closes.

For Hermes this means:

- cross-thread WAL readers do not share TEMP memo state;
- separate `SessionDB` objects do not share TEMP memo state;
- process/connection teardown removes leaked TEMP objects even if explicit DROP was skipped.

### 4.2 `mode=ro` main database + writable TEMP

SQLite's URI docs define `mode=ro` as opening the named database file read-only. Separately, the TEMP docs define TEMP objects as belonging to a distinct per-connection temporary database.

The combination was probed directly (section 7): main-schema `CREATE TABLE` fails, while `CREATE TEMP TABLE`, INSERT, SELECT, and DROP succeed on the same `mode=ro` connection.

So `mode=ro` is not a blocker for TEMP memo on `_read_ctx()`.

### 4.3 Transactions and the snapshot trap

SQLite automatically starts transactions for statements when none is open, and an implicit transaction commits when the last active statement finishes.

Hermes opens both writer and read connections with `isolation_level=None`, so Python does not group our future TEMP statements into one transaction automatically.

That matters because today's winner algorithm is a **single statement**. Replacing it with:

```text
SELECT candidates
CREATE TEMP
SELECT parent
INSERT memo
SELECT parent
UPDATE memo
...
```

without an explicit transaction can observe a changing main database between statements.

The minimal equivalence contract is therefore:

```text
BEGIN            -- deferred read transaction
read candidate/main state (establish snapshot)
CREATE/WRITE TEMP memo on same connection
all main reads + temp reads/writes
COMMIT            -- or ROLLBACK on failure
DROP TEMP IF EXISTS in finally
```

Do **not** use `BEGIN IMMEDIATE`: TEMP memo does not need a main-database write reservation, and that would unnecessarily contend with real writers.

### 4.4 Long read transaction cost

In WAL mode, another writer can commit while the reader keeps its old snapshot; the focused probe confirms this concrete Hermes-like shape. The tradeoff is that a longer-lived WAL read snapshot can delay checkpoint progress past pages still needed by that reader.

In DELETE-journal fallback, a longer read transaction can block external writers longer than today's one SELECT statement. `_read_ctx()` already deliberately falls back to the locked writer path in non-WAL mode because concurrency is less forgiving there. Any TEMP benchmark must report phase duration separately for WAL and fallback if fallback performance matters.

### 4.5 DDL / prepared statements

Creating/dropping a TEMP table changes the TEMP schema on that connection. SQLite documents `SQLITE_SCHEMA` for stale prepared statements and automatic reprepare for statements prepared with `sqlite3_prepare_v2()` (up to the retry limit).

Hermes/Python does not keep a hand-managed prepared statement for the future memo table across queries. Unique dynamic table names will cause Python's statement cache to have low reuse for the DDL/traversal SQL, but this is a performance concern, not a correctness blocker. Keep the generated identifier constrained to a trusted hex/ASCII suffix because SQL identifiers cannot be parameter-bound.

### 4.6 `temp_store` and spill

Hermes applies configured `database.temp_store` to writer, explicit read-only, and WAL reader connections. The setting is therefore **per deployment/config**, not a fixed property of the algorithm.

SQLite documents:

- `PRAGMA temp_store=MEMORY` keeps TEMP tables/indices in memory;
- FILE/default can use a temporary file;
- even when configured for file backing, small temporary tables often remain entirely in the page cache and no temp file is opened until the cache needs to spill.

A ~300-row memo is therefore plausibly memory-resident under common settings, but #45 must not encode that as an invariant. Runtime benchmark telemetry should record `PRAGMA temp_store` and the relevant `SQLITE_TEMP_STORE` compile option.

---

## 5. Cleanup, exceptions, cancellation, and collision

### 5.1 Simplest safe lifecycle

Conceptual shape only — **not production code for #50**:

```python
with one_connection_for_the_whole_winner_phase() as conn:
    temp_name = trusted_unique_hex_name(request_id)
    began = False
    try:
        conn.execute("BEGIN")
        began = True
        # First main read establishes the snapshot used by candidate/traversal.
        conn.execute(f'CREATE TEMP TABLE "{temp_name}" (...)')
        # All candidate, parent, memo and winner statements use conn.
        conn.execute("COMMIT")
        began = False
    except BaseException:
        if began:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute(f'DROP TABLE IF EXISTS temp."{temp_name}"')
```

The final implementation must make rollback/drop themselves best-effort-safe so cleanup errors do not mask the original query error.

### 5.2 Why unique-per-query name A is enough

A unique name solves the only concrete lifecycle hazards without creating shared state:

- stale table left by a failed DROP cannot collide with the next query;
- nested/test/manual reentrancy on the same connection is non-conflicting;
- logs can associate the temp object with a request id;
- no `query_id` column or bulk cleanup predicate is needed.

The table name should be generated by application code from a fixed prefix plus hex request id / random suffix, not caller-controlled text.

### 5.3 Exception between CREATE and DROP

Normal Python exceptions and `BaseException` unwinding execute `finally`, so DROP is attempted. If the connection itself becomes unusable and DROP fails, that connection's later `close()` deletes the entire TEMP database. On the long-lived per-thread reader, a failed DROP on an otherwise healthy connection can leave one orphan table until close; unique naming makes that a bounded-per-failure resource leak rather than a correctness collision. Telemetry should count cleanup failures.

### 5.4 Cancellation / process death

The current search stack is synchronous; there is no async `await` inside the winner phase where an `asyncio.CancelledError` can be injected cooperatively. Thread/process shutdown can still abort execution. Connection close/process teardown is therefore the final cleanup guarantee for TEMP state.

No design should depend on `DROP` having run before a hard process kill.

### 5.5 Why reusable table B is not justified yet

A reusable connection-local table such as:

```text
memo(query_id, node, resolved_root, ...)
```

adds:

- shared mutable state across logical searches on a reused reader;
- mandatory per-query delete/GC;
- query-id filtering on every index/key;
- more failure recovery state;
- harder inspection when a stale row survives.

There is no demonstrated A-specific problem that needs those costs. Do not promote B unless benchmark-v2 shows repeated CREATE/DROP is materially expensive on the actual deployment runtime.

---

## 6. Lock/snapshot implications for #45 contenders

The phrase “TEMP memo adds writes” is misleading unless the target database is named.

### TEMP writes

`INSERT/UPDATE` into a TEMP table write the connection's TEMP database. They do not mutate `main.state.db`.

### Python lock

If the TEMP contender is implemented under current `search_session_winners()`'s `with self._lock:`, it **does** make the same-process lock critical section longer. That can delay transcript writes and other writer-bound reads in the shared `SessionDB` even though the TEMP statements themselves do not take the main DB's write lock.

### Main DB SQLite lock

With WAL + a `mode=ro` reader + deferred `BEGIN`, the contender holds a main read snapshot, not a main write reservation. A real main writer can continue to commit; the reader keeps its old snapshot until it ends the transaction.

### Non-WAL fallback

The fallback connection is `self._conn` under `self._lock`, and rollback-journal read locks are more restrictive. TEMP remains correct but the expanded phase can increase both Python-lock duration and DB read-lock duration. This is a performance/operability concern, not an isolation failure.

---

## 7. Focused runtime probes

These probes were intentionally narrow: validate lifecycle semantics and get an order-of-magnitude DDL/memo overhead floor. They are **not** the benchmark-v2 algorithm comparison.

### 7.1 Probe environment

Research execution environment:

```text
Python:  3.13.5
SQLite:  3.46.1
PRAGMA temp_store: 0 (DEFAULT)
compile option: TEMP_STORE=1
compile option: THREADSAFE=1
```

This environment is not asserted to be identical to the user's deployed Hermes runtime. Performance numbers below are therefore evidence about mechanism/scale only; rerun the same micro-probe at the deployment sync point before a #45 production choice.

### 7.2 Read-only main + TEMP write

Setup:

1. create a normal DB with a `sessions` table;
2. close it;
3. reopen as `file:<path>?mode=ro`, `uri=True`, `isolation_level=None`;
4. create TEMP keyed table;
5. insert 300 rows;
6. query it;
7. attempt main-schema CREATE;
8. drop TEMP.

Observed:

```text
main SELECT: success
CREATE TEMP TABLE: success
300 TEMP INSERTs: success
TEMP SELECT: success
main CREATE TABLE: OperationalError("attempt to write a readonly database")
DROP TEMP TABLE: success
```

No new temp-file FD was observed at 300 rows on this Linux probe, consistent with SQLite's documented behavior that file-backed TEMP data can remain in the page cache until spill is needed.

### 7.3 Snapshot + concurrent writer under WAL

Probe sequence:

```text
reader = mode=ro, isolation_level=None
writer = normal second connection
journal_mode = WAL

reader: BEGIN
reader: SELECT main row -> "old"
reader: CREATE TEMP memo
reader: INSERT memo
writer: UPDATE main row -> "new"; commit
reader: SELECT main row -> still "old"
reader: SELECT temp memo -> success
reader: COMMIT
reader: SELECT main row -> "new"
```

Observed writer commit while reader transaction was open: ~1.38 ms in this run.

This is the exact safety property required by a multi-statement TEMP contender: stable read snapshot + writable private TEMP state + no main writer reservation.

### 7.4 Warm lifecycle overhead

Table shape used:

```sql
CREATE TEMP TABLE <unique_name>(
    node TEXT PRIMARY KEY,
    root TEXT
) WITHOUT ROWID;
```

#### Empty lifecycle

500 warm iterations of `CREATE TEMP keyed table + DROP`:

| metric | ms |
|---|---:|
| median | 0.042 |
| p95 | 0.061 |
| p99 | 0.139 |
|max | 0.309 |

#### Small memo lifecycle

100 warm iterations of:

```text
CREATE
300 UPSERT rows
300 indexed point SELECTs
DROP
```

| metric | ms |
|---|---:|
| median | 1.177 |
| p95 | 1.697 |
| p99 | 2.372 |
|max | 2.535 |

Interpretation:

- create/drop cost alone is tiny on this runtime;
- the total is dominated by the many Python↔SQLite statement crossings, not by DDL;
- a real contender should batch work into set-oriented statements where possible rather than translating every graph edge into one Python-issued SQL call;
- production benchmark must include actual lineage shapes and actual SQLite runtime/config before TEMP is promoted.

---

## 8. Exact later implementation seam

No #45 implementation belongs in this PR. If TEMP survives benchmark-v2, the smallest credible production seam is:

### Seam 1 — `hermes_state_search.py::search_session_winners()`

Current execution tail: `L1673-L1679`.

Replace only the execution/orchestration region needed for the chosen algorithm; keep existing candidate route construction, ordering, exclusion semantics, winner payload shape, and telemetry contract unless the algorithm itself requires a proven change.

Connection ownership must become explicit:

```text
search_session_winners
  -> acquire exactly one connection for complete candidate+lineage+winner phase
  -> BEGIN read transaction
  -> CREATE unique TEMP memo
  -> run contender statements on same conn
  -> produce same winner rows/stats
  -> COMMIT/ROLLBACK
  -> DROP TEMP in finally
```

If the implementation uses `_read_ctx()`, do not open a new `_read_ctx()` per sub-step. One outer context must own the entire TEMP lifetime.

### Seam 2 — `_read_ctx()` interaction, `hermes_state.py:L2197-L2267`

No new connection-pool abstraction is needed for TEMP itself. The existing abstraction already gives:

- WAL: independent per-thread read-only connection;
- fallback: locked writer connection.

The #45 patch should consume that contract, not reach into `_read_local` directly.

### Seam 3 — transaction helper (local/private if useful)

If transaction/cleanup boilerplate becomes nontrivial, use a small private helper local to the search implementation. Do **not** turn #45 into a generic SessionDB transaction-framework refactor.

Required invariants:

- deferred `BEGIN`, not `BEGIN IMMEDIATE`;
- all main reads for winner selection share the same transaction;
- TEMP name is trusted/generated;
- rollback and drop are best-effort cleanup that preserve the original exception;
- no TEMP state leaks into hydration or public result shape.

### Seam 4 — telemetry

At minimum benchmark/diagnostic telemetry should separate:

```text
temp_create_ms
temp_populate/update_ms
lineage_main_read_ms
temp_lookup_ms
temp_drop_ms
winner_phase_ms
connection_route = wal_reader | locked_writer_fallback
transaction_ms
cleanup_failed = bool
```

Also record `PRAGMA temp_store` once per benchmark/runtime receipt, not per production query log.

---

## 9. Acceptance answers for #50

### Does one logical search stay on one connection?

- **Current winner SQL:** yes, one writer connection under one `self._lock` critical section.
- **Current entire `_discover()`:** no. Winner selection and hydration are separate method calls and normally use different physical connections in WAL mode.
- **Future TEMP contender:** it must explicitly keep candidate+lineage+winner work inside one outer connection context. TEMP must not be expected to survive across `_discover()` phases.

### Can two searches interleave on the same connection?

- Current shared-instance winner path: no; `self._lock` serializes it.
- WAL `_read_ctx()`: simultaneous threads get separate thread-local connections, so they overlap on different connections/TEMP schemas.
- Same thread: normal synchronous searches are sequential, but the read connection is reused across calls.
- Separate `SessionDB` instances: separate connections/locks can overlap.

### Is TEMP isolated and cleaned up safely?

Yes, if implemented per query with generated name + `finally DROP IF EXISTS`, and with connection close as final fallback. TEMP is connection-local by SQLite contract.

### What extra correctness rule appears once traversal becomes multi-statement?

One explicit read transaction must span the whole main-DB read phase to preserve the single-statement snapshot semantics of the current algorithm.

### Is `mode=ro` a blocker?

No. Probe shows main writes fail while TEMP create/write/drop succeeds on the same connection. This matches SQLite's separate TEMP-database model.

### Does TEMP necessarily mean disk I/O?

No. `temp_store` is configurable, and SQLite may keep small file-backed TEMP structures in the page cache without opening/spilling a temp file. Do not rely on this; measure the deployment runtime.

### Is lifecycle overhead obviously prohibitive?

No. The local warm create/drop floor is tens of microseconds and a deliberately statement-heavy 300-row/300-lookup lifecycle is ~1-2 ms. The real risk is orchestration/statement count and lock/snapshot duration, not table creation alone.

### Should #45 use reusable connection-local table + `query_id`?

No evidence supports that complexity yet. Keep per-query unique table as the only TEMP shape unless deployment benchmark proves CREATE/DROP is a bottleneck.

---

## 10. Decision for #45

**TEMP memo is not excluded by lifecycle mechanics.** The focused audit removes the two largest unknowns:

- connection-local TEMP works on Hermes-style `mode=ro` readers;
- safe snapshot semantics are attainable with one deferred read transaction while WAL writers continue.

But it also exposes the cost that must be benchmarked correctly:

- current `search_session_winners()` is still writer-connection / `self._lock` bound;
- a direct multi-statement implementation there would lengthen a global same-process critical section;
- moving the contender onto `_read_ctx()` avoids that in WAL, but then the patch must deliberately preserve one main snapshot across its statements;
- non-WAL fallback necessarily retains the longer locked phase.

Therefore the benchmark-v2 TEMP contender should model the **safe final shape**, not an easier but misleading prototype:

```text
one outer _read_ctx connection
+ deferred BEGIN snapshot
+ unique per-query TEMP memo
+ set-oriented traversal statements
+ guaranteed rollback/drop cleanup
```

Compare that against completed-CTE staging. Promote TEMP only if the measured reduction in repeated lineage work beats the extra statement/orchestration cost by enough to justify the lifecycle code. Otherwise retire it with evidence rather than because TEMP was assumed unsafe.
