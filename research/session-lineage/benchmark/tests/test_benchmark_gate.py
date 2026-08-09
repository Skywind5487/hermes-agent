from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fixed3_optimized import fixed3_shared_cross
from gate_sweeps import eqp_probe
from per_seed import per_seed_point
from production_profile import profile_database
from python_memo import python_dict_memo
from scenarios import Builder, Scenario, ScenarioFile, make_correctness_scenarios, make_small_c_scenarios, reference
from temp_memo import pure_temp

ALGORITHMS = [per_seed_point, python_dict_memo, pure_temp, fixed3_shared_cross]


class AlgorithmContractTests(unittest.TestCase):
    def test_correctness_fixtures_match_reference(self):
        for scenario in make_correctness_scenarios():
            with self.subTest(scenario=scenario.name):
                sf = ScenarioFile(scenario, filler=25)
                try:
                    for fn in ALGORITHMS:
                        conn = sf.open()
                        try:
                            ref = reference(conn, scenario.k)
                            result = fn(
                                SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                                scenario.k,
                                10_000,
                            )
                            self.assertEqual(result["roots"], ref, (scenario.name, fn.__name__, result, ref))
                            self.assertFalse(result["bound_hit"], (scenario.name, fn.__name__, result))
                        finally:
                            conn.close()
                finally:
                    sf.close()

    def test_small_c_candidates_are_unique_and_exact(self):
        for scenario in make_small_c_scenarios(values=(3, 10, 30)):
            self.assertEqual(len(scenario.candidates), len(set(scenario.candidates)), scenario.name)
            sf = ScenarioFile(scenario, filler=25)
            try:
                for fn in ALGORITHMS:
                    conn = sf.open()
                    try:
                        ref = reference(conn, scenario.k)
                        result = fn(
                            SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                            scenario.k,
                            10_000,
                        )
                        self.assertEqual(result["roots"], ref, (scenario.name, fn.__name__))
                    finally:
                        conn.close()
            finally:
                sf.close()

    def test_global_budget_never_creates_fake_root(self):
        b = Builder()
        chain = b.chain(100)
        scenario = Scenario("bound100", b.sessions, [chain[-1]], 1, "bound fixture", False)
        sf = ScenarioFile(scenario, filler=0)
        try:
            for fn in ALGORITHMS:
                conn = sf.open()
                try:
                    result = fn(
                        SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                        1,
                        10,
                    )
                    self.assertTrue(result["bound_hit"], fn.__name__)
                    self.assertEqual(result["roots"], [], fn.__name__)
                    self.assertLessEqual(result["work"], 10, fn.__name__)
                finally:
                    conn.close()

                conn = sf.open()
                try:
                    ref = reference(conn, 1)
                    result = fn(
                        SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                        1,
                        200,
                    )
                    self.assertFalse(result["bound_hit"], fn.__name__)
                    self.assertEqual(result["roots"], ref, fn.__name__)
                finally:
                    conn.close()
        finally:
            sf.close()

    def test_fixed_plan_has_no_child_full_scan(self):
        result = eqp_probe(filler=1000)
        self.assertFalse(result["full_child_scan"], result["bad_details"])
        self.assertFalse(result["suspicious_sessions_scan"], result["bad_details"])


class ProductionProfileSafetyTests(unittest.TestCase):
    def make_db(self, path: Path):
        conn = sqlite3.connect(path)
        try:
            conn.executescript('''
                PRAGMA foreign_keys=ON;
                CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,model_config TEXT,source TEXT,started_at TEXT,ended_at TEXT);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id), content TEXT);
                CREATE TABLE gateway_routing(key TEXT PRIMARY KEY, entry_json TEXT);
                INSERT INTO sessions VALUES('root',NULL,'compression','{}','discord','1','2');
                INSERT INTO sessions VALUES('tip','root',NULL,'{}','discord','3',NULL);
                INSERT INTO sessions VALUES('branch','root',NULL,'{"_branched_from":"root"}','discord','3',NULL);
                INSERT INTO messages(session_id,content) VALUES('tip','x');
                INSERT INTO gateway_routing VALUES('k','{}');
            ''')
            conn.commit()
        finally:
            conn.close()

    def test_profiler_is_read_only_and_classifies_topology(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.db"
            self.make_db(path)
            before = path.read_bytes()
            expected = hashlib.sha256(before).hexdigest()
            report = profile_database(path, expected, enforce_authoritative_counts=False)
            after = path.read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(report["source"]["sha256"], expected)
            self.assertFalse(report["source"]["mutations_performed"])
            self.assertEqual(report["canonical_counts"]["sessions"], 3)
            self.assertEqual(report["topology"]["positive_compression_edges"], 1)
            self.assertEqual(report["topology"]["child_markers"].get("branch_marker"), 1)
            self.assertFalse(report["query_distribution"]["measured"])

    def test_profiler_handles_tail_into_positive_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.db"
            self.make_db(path)
            conn = sqlite3.connect(path)
            try:
                conn.executemany(
                    "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                    [
                        ("cyc1", "cyc2", "compression", "{}", "discord", "4", "5"),
                        ("cyc2", "cyc1", "compression", "{}", "discord", "4", "5"),
                        ("tail", "cyc1", None, "{}", "discord", "6", None),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            report = profile_database(path, expected, enforce_authoritative_counts=False)
            self.assertEqual(report["topology"]["positive_lineage_cycles"], 2)

    def test_profiler_rejects_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.db"
            self.make_db(path)
            with self.assertRaises(SystemExit):
                profile_database(path, "0" * 64, enforce_authoritative_counts=False)


if __name__ == "__main__":
    unittest.main()
