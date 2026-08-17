from __future__ import annotations

import csv
import math
import resource
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fixed3_optimized import FIXED3_SQL, fixed3_params, fixed3_shared_cross
from per_seed import per_seed_point
from scenarios import Scenario, ScenarioFile, make_full_consume_scenarios, make_pathological_scenarios, make_perf_scenarios, make_small_c_scenarios, reference
from temp_memo import pure_temp

ALGORITHMS = [("per_seed_point", per_seed_point), ("pure_temp", pure_temp), ("fixed3_shared_bound", fixed3_shared_cross)]
FINALISTS = ALGORITHMS[1:]


def percentile(values, p):
    ordered = sorted(values)
    if not ordered: return float("nan")
    x = (len(ordered) - 1) * p; lo, hi = math.floor(x), math.ceil(x)
    if lo == hi: return ordered[lo]
    return ordered[lo] * (hi - x) + ordered[hi] * (x - lo)


def stats(values):
    return {"mean_ms": statistics.fmean(values), "median_ms": statistics.median(values), "p95_ms": percentile(values, 0.95), "stdev_ms": statistics.pstdev(values), "min_ms": min(values), "max_ms": max(values)}


def rss_kb():
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"): return int(line.split()[1])
    except (FileNotFoundError, ValueError): pass
    return None


def maxrss_kb():
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def run_once(conn, scenario: Scenario, fn, B: int, *, observe_temp=False, after_snapshot_read=None):
    db = SimpleNamespace(conn=conn, scenario=scenario, observe_temp=observe_temp, after_snapshot_read=after_snapshot_read)
    ref = reference(conn, scenario.k); before = rss_kb(); started = time.perf_counter_ns()
    result = fn(db, scenario.k, B); elapsed_ms = (time.perf_counter_ns() - started) / 1e6; after = rss_kb()
    return {"elapsed_ms": elapsed_ms, "exact": result["roots"] == ref, "reference_roots": ref, "rss_kb_before": before, "rss_kb_after": after, "maxrss_kb": maxrss_kb(), **result}


def repeated(scenario, fn, *, filler=20_000, B=10_000, repeats=5, journal="WAL", temp_store="MEMORY", warmup=True):
    sf = ScenarioFile(scenario, filler, journal_mode=journal, temp_store=temp_store, reader_mode="ro" if journal == "WAL" else "rw")
    try:
        conn = sf.open()
        try:
            if warmup: run_once(conn, scenario, fn, B)
            values=[]; last=None
            for _ in range(repeats): last=run_once(conn, scenario, fn, B); values.append(last["elapsed_ms"])
            return {**stats(values), **{k:last[k] for k in ("exact","work","bound_hit","statements","candidates","temp_peak_bytes","rss_kb_before","rss_kb_after","maxrss_kb")}}
        finally: conn.close()
    finally: sf.close()


def small_c_sweep(*, repeats=7, filler=20_000, B=10_000, values=(3,5,10,20,30,50,100,300)):
    rows=[]
    for scenario in make_small_c_scenarios(values):
        for name,fn in ALGORITHMS:
            result=repeated(scenario,fn,filler=filler,B=B,repeats=repeats)
            rows.append({"scenario":scenario.name,"candidates_input":len(scenario.candidates),"k":scenario.k,"algorithm":name,"filler":filler,"budget":B,**result})
    return rows


def full_consume_sweep(*, repeats=7, filler=20_000, B=10_000, values=(5,10,15,20,25,30)):
    rows=[]
    for scenario in make_full_consume_scenarios(values):
        for name,fn in ALGORITHMS:
            result=repeated(scenario,fn,filler=filler,B=B,repeats=repeats)
            rows.append({"scenario":scenario.name,"candidates_input":len(scenario.candidates),"k":scenario.k,"algorithm":name,"filler":filler,"budget":B,**result})
    return rows


def budget_sweep(*, repeats=3, filler=0, budgets=(64,128,256,512,1000,2000,5000,10000,20000)):
    rows=[]
    for scenario in make_pathological_scenarios():
        for B in budgets:
            for name,fn in ALGORITHMS:
                result=repeated(scenario,fn,filler=filler,B=B,repeats=repeats,warmup=False)
                rows.append({"scenario":scenario.name,"candidates_input":len(scenario.candidates),"k":scenario.k,"algorithm":name,"filler":filler,"budget":B,**result})
    return rows


def scale_sweep(*, repeats=5, fillers=(0,20_000,250_000), B=10_000):
    scenarios={s.name:s for s in make_perf_scenarios()}; selected=[scenarios["modern_roots_k3"],scenarios["blocked_50_lineages_depth5_k3"]]; rows=[]
    for filler in fillers:
        for scenario in selected:
            for name,fn in FINALISTS:
                result=repeated(scenario,fn,filler=filler,B=B,repeats=repeats)
                rows.append({"scenario":scenario.name,"candidates_input":len(scenario.candidates),"k":scenario.k,"algorithm":name,"filler":filler,"budget":B,**result})
    return rows


def lifecycle_probe(*, calls=8, filler=20_000, B=10_000):
    scenarios={s.name:s for s in make_perf_scenarios()}; selected=[scenarios["modern_roots_k3"],scenarios["blocked_50_lineages_depth5_k3"]]; rows=[]
    for scenario in selected:
        sf=ScenarioFile(scenario,filler,journal_mode="WAL",temp_store="MEMORY",reader_mode="ro")
        try:
            for name,fn in FINALISTS:
                conn=sf.open()
                try:
                    for invocation in range(1,calls+1):
                        result=run_once(conn,scenario,fn,B)
                        rows.append({"scenario":scenario.name,"algorithm":name,"invocation":invocation,"connection_age":"first" if invocation==1 else ("second" if invocation==2 else "warm_reuse"),"filler":filler,"budget":B,**{k:result[k] for k in ("elapsed_ms","exact","work","bound_hit","statements","candidates","temp_peak_bytes","rss_kb_before","rss_kb_after","maxrss_kb")}})
                finally: conn.close()
        finally: sf.close()
    return rows


def _writer_attempt(path,start_event,result_box):
    start_event.wait(); conn=sqlite3.connect(path,isolation_level=None,timeout=5.0)
    try:
        started=time.perf_counter_ns()
        try:
            conn.execute("BEGIN IMMEDIATE"); conn.execute("UPDATE probe_counter SET value=value+1 WHERE id=1"); conn.execute("COMMIT"); result_box["ok"]=True
        except BaseException as exc:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            result_box["ok"]=False; result_box["error"]=repr(exc)
        result_box["writer_ms"]=(time.perf_counter_ns()-started)/1e6
    finally: conn.close()


def nonwal_fallback_probe(*, repeats=5, filler=20_000, B=10_000):
    scenarios={s.name:s for s in make_perf_scenarios()}; scenario=scenarios["blocked_50_lineages_depth5_k3"]; rows=[]
    for name,fn in FINALISTS:
        sf=ScenarioFile(scenario,filler,journal_mode="DELETE",temp_store="MEMORY",reader_mode="rw"); py_lock=threading.Lock(); conn=sf.open()
        try:
            for iteration in range(1,repeats+1):
                lock_wait={}; writer_box={}; writer_start=threading.Event(); lock_wait_start=threading.Event()
                def lock_waiter():
                    lock_wait_start.wait(); started=time.perf_counter_ns()
                    with py_lock: pass
                    lock_wait["ms"]=(time.perf_counter_ns()-started)/1e6
                waiter=threading.Thread(target=lock_waiter,daemon=True); writer=threading.Thread(target=_writer_attempt,args=(sf.path,writer_start,writer_box),daemon=True)
                py_lock.acquire(); waiter.start(); writer.start(); lock_wait_start.set()
                try:
                    if name=="pure_temp": result=run_once(conn,scenario,fn,B,after_snapshot_read=writer_start.set)
                    else:
                        fired=False
                        def progress():
                            nonlocal fired
                            if not fired: fired=True; writer_start.set()
                            return 0
                        conn.set_progress_handler(progress,1000)
                        try: result=run_once(conn,scenario,fn,B)
                        finally:
                            conn.set_progress_handler(None,0)
                            if not fired: writer_start.set()
                finally: py_lock.release()
                waiter.join(timeout=5); writer.join(timeout=5)
                rows.append({"scenario":scenario.name,"algorithm":name,"iteration":iteration,"connection_age":"first" if iteration==1 else ("second" if iteration==2 else "warm_reuse"),"journal_mode":"DELETE","route":"locked_writer_fallback","phase_ms":result["elapsed_ms"],"python_lock_wait_ms":lock_wait.get("ms"),"competing_writer_ms":writer_box.get("writer_ms"),"competing_writer_ok":writer_box.get("ok"),"competing_writer_error":writer_box.get("error",""),"exact":result["exact"],"work":result["work"],"statements":result["statements"]})
        finally: conn.close(); sf.close()
    return rows


def temp_store_sweep(*, repeats=5, filler=20_000, B=10_000, stores=("DEFAULT","FILE","MEMORY")):
    scenarios={s.name:s for s in make_perf_scenarios()}; selected=[scenarios["blocked_50_lineages_depth5_k3"],scenarios["malformed_fanout_stress_k3"]]; rows=[]
    for store in stores:
        for scenario in selected:
            sf=ScenarioFile(scenario,filler,journal_mode="WAL",temp_store=store,reader_mode="ro")
            try:
                conn=sf.open()
                try:
                    values=[]; last=None
                    for _ in range(repeats): last=run_once(conn,scenario,pure_temp,B,observe_temp=True); values.append(last["elapsed_ms"])
                    rows.append({"scenario":scenario.name,"algorithm":"pure_temp","temp_store":store,"actual_temp_store":int(conn.execute("PRAGMA temp_store").fetchone()[0]),"filler":filler,"budget":B,**stats(values),**{k:last[k] for k in ("exact","work","statements","temp_peak_bytes","rss_kb_before","rss_kb_after","maxrss_kb")}})
                finally: conn.close()
            finally: sf.close()
    return rows


def eqp_probe(*, filler=250_000, B=10_000):
    scenario=next(s for s in make_perf_scenarios() if s.name=="blocked_50_lineages_depth5_k3"); sf=ScenarioFile(scenario,filler,journal_mode="WAL",temp_store="MEMORY",reader_mode="ro")
    try:
        conn=sf.open()
        try: plan=[dict(row) for row in conn.execute("EXPLAIN QUERY PLAN "+FIXED3_SQL,fixed3_params(scenario.k,B))]
        finally: conn.close()
    finally: sf.close()
    details=[str(row.get("detail","")) for row in plan]; bad=[d for d in details if "SCAN child" in d]; suspicious=[d for d in details if "SCAN sessions" in d and "USING" not in d]
    return {"scenario":scenario.name,"filler":filler,"full_child_scan":bool(bad),"suspicious_sessions_scan":bool(suspicious),"bad_details":bad+suspicious,"plan":plan}


def write_csv(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if not rows: path.write_text("",encoding="utf-8"); return
    fields=[]; seen=set()
    for row in rows:
        for key in row:
            if key not in seen: seen.add(key); fields.append(key)
    with path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
