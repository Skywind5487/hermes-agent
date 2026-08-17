from __future__ import annotations

import unittest
from types import SimpleNamespace

from fixed3_optimized import fixed3_shared_cross
from focused_scenarios import make_focused_scenarios
from per_seed import per_seed_point
from python_memo import python_dict_memo
from scenarios import Builder, Scenario, ScenarioFile, reference
from temp_memo import pure_temp

ALGORITHMS = [per_seed_point, python_dict_memo, pure_temp, fixed3_shared_cross]


class FocusedGateTests(unittest.TestCase):
    def test_focused_scenarios_match_reference(self):
        for scenario in make_focused_scenarios():
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
                            self.assertEqual(result["roots"], ref, (scenario.name, fn.__name__))
                            self.assertFalse(result["bound_hit"], (scenario.name, fn.__name__, result))
                        finally:
                            conn.close()
                finally:
                    sf.close()

    def test_real_historical_fixture_is_depth14_size15(self):
        scenario = next(
            s for s in make_focused_scenarios()
            if s.name == "focused_historical_depth14_size15_worst_k3"
        )
        self.assertEqual(len(scenario.candidates), 17)
        self.assertEqual(len(scenario.sessions), 17)

        parent = {sid: pid for sid, pid, _ in scenario.sessions}
        cur = scenario.candidates[0]
        depth = 0
        while parent[cur] is not None:
            depth += 1
            cur = parent[cur]
        self.assertEqual(depth, 14)

    def test_python_memo_reuses_historical_ancestry(self):
        scenario = next(
            s for s in make_focused_scenarios()
            if s.name == "focused_historical_depth14_size15_fullconsume_k3"
        )
        sf = ScenarioFile(scenario, filler=0)
        try:
            conn = sf.open()
            try:
                no_memo = per_seed_point(
                    SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                    scenario.k,
                    10_000,
                )
            finally:
                conn.close()

            conn = sf.open()
            try:
                memo = python_dict_memo(
                    SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                    scenario.k,
                    10_000,
                )
            finally:
                conn.close()

            self.assertEqual(no_memo["roots"], memo["roots"])
            self.assertEqual(no_memo["work"], 120)
            self.assertEqual(memo["work"], 15)
            self.assertEqual(memo["memo_entries"], 15)
        finally:
            sf.close()

    def test_python_memo_budget_fails_closed(self):
        b = Builder()
        chain = b.chain(100)
        scenario = Scenario("memo_bound", b.sessions, [chain[-1]], 1, "memo bound", False)
        sf = ScenarioFile(scenario, filler=0)
        try:
            conn = sf.open()
            try:
                result = python_dict_memo(
                    SimpleNamespace(conn=conn, observe_temp=False, after_snapshot_read=None),
                    1,
                    10,
                )
                self.assertTrue(result["bound_hit"])
                self.assertEqual(result["roots"], [])
                self.assertEqual(result["work"], 10)
            finally:
                conn.close()
        finally:
            sf.close()


if __name__ == "__main__":
    unittest.main()
