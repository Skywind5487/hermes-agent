from __future__ import annotations

import os
import random
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Optional

SEED = 5487


@dataclass
class Scenario:
    name: str
    sessions: list[tuple[str, Optional[str], str]]
    candidates: list[str]
    k: int
    description: str
    performance: bool = True


class Builder:
    def __init__(self):
        self.sessions: list[tuple[str, Optional[str], str]] = []
        self.n = 0

    def new_id(self, prefix: str = "s") -> str:
        self.n += 1
        return f"{prefix}{self.n:07d}"

    def root(self) -> str:
        node = self.new_id()
        self.sessions.append((node, None, "root"))
        return node

    def child(self, parent: str, kind: str = "compression") -> str:
        node = self.new_id()
        self.sessions.append((node, parent, kind))
        return node

    def chain(self, depth: int) -> list[str]:
        nodes = [self.root()]
        for _ in range(depth):
            nodes.append(self.child(nodes[-1], "compression"))
        return nodes


def uniq(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def make_perf_scenarios(C: int = 300) -> list[Scenario]:
    out: list[Scenario] = []

    b = Builder(); c = [b.root() for _ in range(C)]
    out.append(Scenario("modern_roots_k3", b.sessions, c, 3, f"{C} distinct modern/in-place roots; K immediately"))

    b = Builder(); c = [b.root() for _ in range(C)]
    out.append(Scenario("modern_roots_k10", b.sessions, c, 10, f"{C} distinct modern/in-place roots; K=10 early"))

    for depth in (1, 3, 5):
        b = Builder(); c = [b.chain(depth)[-1] for _ in range(C)]
        out.append(Scenario(f"independent_depth{depth}_k3", b.sessions, c, 3, f"{C} independent legacy chains depth {depth}; K early"))

    b = Builder(); chains = [b.chain(5) for _ in range(50)]; c = []
    for ch in chains:
        c.extend(reversed(ch))
    out.append(Scenario("blocked_50_lineages_depth5_k3", b.sessions, c[:C], 3, "50 depth-5 lineages in ranked blocks; Kth lineage begins at candidate 13"))

    b = Builder(); chains = [b.chain(5) for _ in range(50)]; c = []
    for level in range(5, -1, -1):
        for ch in chains:
            c.append(ch[level])
    out.append(Scenario("interleaved_50_lineages_depth5_k3", b.sessions, c[:C], 3, "50 depth-5 lineages interleaved; first 3 candidates are different roots"))

    b = Builder(); chains = [b.chain(5) for _ in range(50)]; c = []
    for ch in chains:
        c.extend(reversed(ch))
    out.append(Scenario("blocked_50_lineages_depth5_k10", b.sessions, c[:C], 10, "50 depth-5 lineages in blocks; K=10 arrives around candidate 55"))

    b = Builder(); chains = [b.chain(5) for _ in range(5)]; c = []
    for ch in chains:
        c.extend(reversed(ch))
    out.append(Scenario("five_lineages_k10_unreachable", b.sessions, c, 10, "Only 5 lineages / 30 distinct sessions; K=10 unreachable"))

    b = Builder(); a = b.chain(5); d = b.chain(5); c = list(reversed(a)) + list(reversed(d)); third = b.chain(5); c.append(third[-1])
    while len(c) < C:
        ch = b.chain(5)
        for node in reversed(ch):
            if len(c) >= C:
                break
            c.append(node)
    out.append(Scenario("kth_at_13_then_depth5_tail_k3", b.sessions, c[:C], 3, "First 12 candidates are exactly 2 lineages; candidate 13 is Kth; realistic depth5 tail follows"))

    b = Builder(); a = b.chain(5); r2 = b.chain(5); r3 = b.chain(5); c = list(reversed(a))[:5]
    c += list(reversed(r2))[:4] + list(reversed(r3))[:3]
    while len(c) < C:
        ch = b.chain(random.Random(len(c) + SEED).randint(0, 5))
        for node in reversed(ch):
            if len(c) >= C:
                break
            c.append(node)
    out.append(Scenario("speculation_fails_shared_middle_realistic_k3", b.sessions, uniq(c)[:C], 3, "TEMP-sized prefix collapses; next shared-sized batch contains two new legacy lineages"))

    rng = random.Random(1001); b = Builder(); c = []
    while len(c) < C:
        depth = 0 if rng.random() < 0.78 else rng.randint(1, 5)
        ch = b.chain(depth); c.append(ch[-1])
        if depth and rng.random() < 0.35 and len(c) < C:
            c.append(ch[rng.randrange(len(ch) - 1)])
    out.append(Scenario("modern_sparse_legacy_mix_k3", b.sessions, uniq(c)[:C], 3, "~78% modern roots, sparse depth1..5 legacy continuations"))

    for seed in (11, 22, 33):
        rng = random.Random(seed); b = Builder(); chains = [b.chain(rng.randint(0, 5)) for _ in range(90)]; pool = []
        for i, ch in enumerate(chains):
            weight = 1 / (i + 1) ** 1.1
            for node in reversed(ch):
                pool.append((rng.random() / weight, node))
        pool.sort(); c = uniq([node for _, node in pool])[:C]
        out.append(Scenario(f"random_zipf_realistic_{seed}_k3", b.sessions, c, 3, "Random clustered realistic depth0..5 candidates, unique session IDs"))

    b = Builder(); root = b.root(); trunk = b.child(root, "compression"); c = []
    for _ in range(C):
        node = b.child(trunk, "compression")
        if len(c) % 2 == 0:
            node = b.child(node, "compression")
        c.append(node)
    out.append(Scenario("malformed_fanout_stress_k3", b.sessions, c, 3, "Many compression children share one parent; stress/compat topology, not normal modern workload"))

    b = Builder(); ch = b.chain(1000)
    out.append(Scenario("safety_depth1000_bound", b.sessions, [ch[-1]], 3, "Pathological acyclic chain for global-bound safety only", False))
    return out


def make_correctness_scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    b = Builder(); root = b.root(); out.append(Scenario("correct_root", b.sessions, [root], 1, "root", False))
    b = Builder(); ch = b.chain(5); out.append(Scenario("correct_compression", b.sessions, [ch[-1]], 1, "depth5 compression", False))
    for kind in ("fork", "delegation", "tool"):
        b = Builder(); parent = b.root(); boundary = b.child(parent, kind); child = b.child(boundary, "compression")
        out.append(Scenario(f"correct_{kind}_boundary", b.sessions, [child], 1, f"{kind} starts lineage", False))
    b = Builder(); node = b.new_id(); b.sessions.append((node, "missing", "compression")); out.append(Scenario("correct_missing_parent", b.sessions, [node], 1, "unresolved missing parent", False))
    b = Builder(); a = b.new_id(); z = b.new_id(); b.sessions.extend([(a, z, "compression"), (z, a, "compression")]); out.append(Scenario("correct_cycle", b.sessions, [a], 1, "unresolved cycle", False))
    return out


def make_small_c_scenarios(values=(3, 5, 10, 20, 30, 50, 100, 300)) -> list[Scenario]:
    """Crossover family where small fixed costs can dominate lineage work."""
    out: list[Scenario] = []
    for C in values:
        b = Builder(); c = [b.chain(3)[-1] for _ in range(C)]
        out.append(Scenario(f"smallc_independent_c{C}_k3", b.sessions, c, min(3, C), f"C={C}, independent depth3, K=min(3,C)"))

        nlineages = max(1, (C + 5) // 6)
        b = Builder(); chains = [b.chain(5) for _ in range(nlineages)]; c = []
        for ch in chains:
            c.extend(reversed(ch))
        k = min(3, nlineages)
        out.append(Scenario(f"smallc_clustered_c{C}_k{k}", b.sessions, c[:C], k, f"C={C}, dense blocked depth5 lineages, K={k}"))
    return out


def make_full_consume_scenarios(values=(5, 10, 15, 20, 25, 30)) -> list[Scenario]:
    out: list[Scenario] = []
    for C in values:
        b = Builder(); chains = [b.chain(5) for _ in range(5)]; pool: list[str] = []
        for ch in chains:
            pool.extend(reversed(ch))
        candidates = pool[: min(C, len(pool))]
        out.append(Scenario(f"full_consume_c{len(candidates)}_k10", b.sessions, candidates, 10, f"C={len(candidates)}, only five lineages; K=10 unreachable"))
    return out


def make_pathological_scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    b = Builder(); ch = b.chain(10_000)
    out.append(Scenario("path_depth10000_single", b.sessions, [ch[-1]], 1, "10k-hop acyclic chain; safety/B cost curve", False))
    b = Builder(); ch = b.chain(5_000)
    candidates = list(reversed(ch[-300:]))
    out.append(Scenario("path_depth5000_concentrated_c300", b.sessions, candidates, 3, "5k-hop single lineage with C=300; long + concentrated stress", False))
    return out


class ScenarioFile:
    def __init__(self, scenario: Scenario, filler: int = 20_000, *, journal_mode: str = "WAL", temp_store: str = "MEMORY", reader_mode: str = "ro"):
        self.s = scenario
        self.filler = filler
        self.journal_mode = journal_mode.upper()
        self.temp_store = temp_store.upper()
        self.reader_mode = reader_mode
        fd, self.path = tempfile.mkstemp(prefix="hermes-lineage-", suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        actual_journal = str(conn.execute(f"PRAGMA journal_mode={self.journal_mode}").fetchone()[0]).upper()
        if actual_journal != self.journal_mode:
            conn.close(); raise RuntimeError(f"journal_mode requested={self.journal_mode} actual={actual_journal}")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript("""
            CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_id TEXT,edge_kind TEXT NOT NULL);
            CREATE INDEX sessions_parent_idx ON sessions(parent_id);
            CREATE TABLE candidates(ord INTEGER PRIMARY KEY,session_id TEXT NOT NULL UNIQUE);
            CREATE TABLE probe_counter(id INTEGER PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
            INSERT INTO probe_counter(id,value) VALUES(1,0);
        """)
        conn.execute("BEGIN")
        conn.executemany("INSERT INTO sessions VALUES(?,?,?)", scenario.sessions)
        filler_rows = [(f"f{((i * 2654435761) & 0xffffffff):08x}_{i:06d}", None, "root") for i in range(filler)]
        conn.executemany("INSERT INTO sessions VALUES(?,?,?)", filler_rows)
        unique_candidates = uniq(scenario.candidates)
        if len(unique_candidates) != len(scenario.candidates):
            raise AssertionError(f"duplicate candidates in {scenario.name}")
        conn.executemany("INSERT INTO candidates VALUES(?,?)", [(i + 1, node) for i, node in enumerate(unique_candidates)])
        conn.execute("COMMIT")
        if self.journal_mode == "WAL":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

    def fadvise_drop(self) -> None:
        if not hasattr(os, "posix_fadvise"):
            return
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)

    def open(self) -> sqlite3.Connection:
        if self.reader_mode == "ro":
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, isolation_level=None)
        elif self.reader_mode == "rw":
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0, check_same_thread=False)
        else:
            raise ValueError(f"unknown reader_mode={self.reader_mode}")
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA temp_store={self.temp_store}")
        conn.execute("PRAGMA cache_size=-32768")
        return conn

    def close(self) -> None:
        for path in (self.path, self.path + "-wal", self.path + "-shm", self.path + "-journal"):
            try: os.remove(path)
            except FileNotFoundError: pass


def reference(conn: sqlite3.Connection, K: int) -> list[str]:
    roots: list[str] = []
    seen_roots: set[str] = set()
    memo: dict[str, Optional[str]] = {}
    for candidate in conn.execute("SELECT session_id FROM candidates ORDER BY ord"):
        cur = candidate["session_id"]; path: list[str] = []; local: set[str] = set(); resolved: Optional[str] = None
        while True:
            if cur in memo:
                resolved = memo[cur]; break
            if cur in local:
                resolved = None; break
            local.add(cur); path.append(cur)
            row = conn.execute("SELECT parent_id,edge_kind FROM sessions WHERE id=?", (cur,)).fetchone()
            if not row:
                resolved = None; break
            if row["edge_kind"] != "compression" or row["parent_id"] is None:
                resolved = cur; break
            if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (row["parent_id"],)).fetchone():
                resolved = None; break
            cur = row["parent_id"]
        if resolved is not None:
            for node in path: memo[node] = resolved
        if resolved is not None and resolved not in seen_roots:
            seen_roots.add(resolved); roots.append(resolved)
            if len(roots) >= K: break
    return roots
