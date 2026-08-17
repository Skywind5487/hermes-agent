from __future__ import annotations


def per_seed_point(db, K: int, B: int):
    """Sequential point-read lower bound: ranked early-stop, no memo/TEMP.

    This is an ablation/crossover probe, not a third production finalist. It still
    preserves the multi-statement single-snapshot contract with one deferred read
    transaction so its speed is not bought by weaker consistency semantics.
    """
    conn = db.conn
    roots = []
    rootset = set()
    work = 0
    statements = 0
    inspected = 0
    bound_hit = False
    began = False

    try:
        conn.execute("BEGIN"); statements += 1; began = True
        cursor = conn.execute("SELECT session_id FROM candidates ORDER BY ord"); statements += 1
        try:
            for candidate in cursor:
                inspected += 1
                cur = candidate["session_id"]
                local = set()
                resolved = None
                while True:
                    if cur in local:
                        resolved = None
                        break
                    if work >= B:
                        bound_hit = True
                        break
                    local.add(cur)
                    row = conn.execute(
                        """SELECT s.parent_id,s.edge_kind,
                                  CASE WHEN p.id IS NULL THEN 0 ELSE 1 END AS parent_exists
                           FROM sessions s LEFT JOIN sessions p ON p.id=s.parent_id
                           WHERE s.id=?""",
                        (cur,),
                    ).fetchone()
                    statements += 1
                    if not row:
                        resolved = None
                        break
                    work += 1
                    if row["edge_kind"] != "compression" or row["parent_id"] is None:
                        resolved = cur
                        break
                    if not row["parent_exists"]:
                        resolved = None
                        break
                    cur = row["parent_id"]

                if bound_hit:
                    break
                if resolved is not None and resolved not in rootset:
                    rootset.add(resolved)
                    roots.append(resolved)
                    if len(roots) >= K:
                        break
        finally:
            cursor.close()
        conn.execute("COMMIT"); statements += 1; began = False
    except BaseException:
        if began:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise

    return {
        "roots": roots[:K],
        "work": work,
        "bound_hit": bound_hit,
        "statements": statements,
        "candidates": inspected,
        "temp_peak_bytes": 0,
    }
