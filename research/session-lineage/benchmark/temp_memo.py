from __future__ import annotations

import itertools


class TraceCounter:
    def __init__(self, conn):
        self.conn = conn
        self.count = 0

    def __enter__(self):
        self.conn.set_trace_callback(self._cb)
        return self

    def _cb(self, _sql):
        self.count += 1

    def __exit__(self, *_args):
        self.conn.set_trace_callback(None)


_NAMES = itertools.count(1)


def _temp_bytes(conn) -> int:
    page_count = int(conn.execute("PRAGMA temp.page_count").fetchone()[0])
    page_size = int(conn.execute("PRAGMA temp.page_size").fetchone()[0])
    return page_count * page_size


def pure_temp(db, K: int, B: int):
    conn = db.conn
    table = f"lineage_memo_{next(_NAMES):016x}"
    quoted = '"' + table + '"'
    temp_peak_bytes = 0

    with TraceCounter(conn) as tc:
        conn.execute("BEGIN")
        began = True
        try:
            conn.execute(f"CREATE TEMP TABLE {quoted}(node TEXT PRIMARY KEY,root TEXT NOT NULL) WITHOUT ROWID")
            roots = []
            rootset = set()
            work = 0
            inspected = 0
            bound_hit = False
            cursor = conn.execute("SELECT ord,session_id FROM candidates ORDER BY ord")
            hook = getattr(db, "after_snapshot_read", None)
            if hook is not None:
                hook()
            try:
                for candidate in cursor:
                    inspected += 1
                    cur = candidate["session_id"]
                    path = []
                    local = set()
                    resolved = None
                    while True:
                        row = conn.execute(
                            f"""SELECT s.parent_id,s.edge_kind,m.root cached_root
                            FROM sessions s LEFT JOIN temp.{quoted} m ON m.node=s.id WHERE s.id=?""",
                            (cur,),
                        ).fetchone()
                        if not row:
                            break
                        if row["cached_root"] is not None:
                            resolved = row["cached_root"]
                            break
                        if cur in local:
                            break
                        if work >= B:
                            bound_hit = True
                            break
                        local.add(cur)
                        path.append(cur)
                        work += 1
                        if row["edge_kind"] != "compression" or row["parent_id"] is None:
                            resolved = cur
                            break
                        cur = row["parent_id"]
                    if bound_hit:
                        break
                    if resolved is not None:
                        conn.executemany(
                            f"INSERT OR IGNORE INTO temp.{quoted}(node,root) VALUES(?,?)",
                            [(node, resolved) for node in path],
                        )
                        if resolved not in rootset:
                            rootset.add(resolved)
                            roots.append(resolved)
                            if len(roots) >= K:
                                break
            finally:
                cursor.close()

            if getattr(db, "observe_temp", False):
                temp_peak_bytes = _temp_bytes(conn)
            conn.execute("COMMIT")
            began = False
        except BaseException:
            if began:
                try: conn.execute("ROLLBACK")
                except Exception: pass
            try: conn.execute(f"DROP TABLE IF EXISTS temp.{quoted}")
            except Exception: pass
            raise
        finally:
            if not began:
                conn.execute(f"DROP TABLE IF EXISTS temp.{quoted}")

    return {"roots": roots[:K], "work": work, "bound_hit": bound_hit, "statements": tc.count, "candidates": inspected, "temp_peak_bytes": temp_peak_bytes}
