#!/usr/bin/env python3
"""Production-shaped synthetic gate for e2-micro and WSL.

This script NEVER opens a production state.db. It builds disposable synthetic DBs,
records the exact runtime/machine receipt, then fills #54 crossover/resource/planner/
non-WAL/lifecycle cells.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from gate_sweeps import budget_sweep, eqp_probe, full_consume_sweep, lifecycle_probe, nonwal_fallback_probe, scale_sweep, small_c_sweep, temp_store_sweep, write_csv

HERE=Path(__file__).resolve().parent


def run_text(cmd,cwd=None):
    try: return subprocess.run(cmd,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False,timeout=20).stdout.strip()
    except Exception as exc: return f"ERROR: {exc!r}"


def read_text(path):
    try: return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception: return ""


def parse_meminfo():
    result={}
    for line in read_text("/proc/meminfo").splitlines():
        if ":" in line:
            key,value=line.split(":",1); result[key]=value.strip()
    return result


def cpu_model():
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"): return line.split(":",1)[1].strip()
    return platform.processor()


def sqlite_receipt():
    conn=sqlite3.connect(":memory:")
    try:
        return {"sqlite_version":sqlite3.sqlite_version,"sqlite_source_id":conn.execute("select sqlite_source_id()").fetchone()[0],"compile_options":[r[0] for r in conn.execute("pragma compile_options")],"pragmas":{"temp_store":conn.execute("pragma temp_store").fetchone()[0],"cache_size":conn.execute("pragma cache_size").fetchone()[0],"synchronous":conn.execute("pragma synchronous").fetchone()[0],"page_size":conn.execute("pragma page_size").fetchone()[0]}}
    finally: conn.close()


def disk_receipt(path):
    usage=shutil.disk_usage(path); return {"path":str(path),"total":usage.total,"used":usage.used,"free":usage.free}


def machine_receipt():
    try: affinity=sorted(os.sched_getaffinity(0))
    except (AttributeError,OSError): affinity=None
    cwd=Path.cwd()
    return {
        "timestamp_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "hostname":platform.node(),"platform":platform.platform(),"uname":list(platform.uname()),
        "python":{"executable":sys.executable,"version":sys.version,"implementation":platform.python_implementation(),"compiler":platform.python_compiler()},
        "sqlite":sqlite_receipt(),
        "cpu":{"logical_count":os.cpu_count(),"affinity":affinity,"model":cpu_model()},
        "memory":parse_meminfo(),
        "cgroup":{"self":read_text("/proc/self/cgroup").strip(),"cpu_max":read_text("/sys/fs/cgroup/cpu.max").strip(),"memory_max":read_text("/sys/fs/cgroup/memory.max").strip(),"memory_current":read_text("/sys/fs/cgroup/memory.current").strip()},
        "system":{"loadavg":read_text("/proc/loadavg").strip(),"uptime":read_text("/proc/uptime").strip(),"dmi_product":read_text("/sys/devices/virtual/dmi/id/product_name").strip(),"cwd_mount":run_text(["findmnt","-T",str(cwd),"-o","SOURCE,FSTYPE,OPTIONS","-n"])},
        "gateway_service":{"show":run_text(["systemctl","--user","show","hermes-gateway.service","--property=ActiveState,MainPID,ExecStart"]),"cat":run_text(["systemctl","--user","cat","hermes-gateway.service"])},
        "disk":{"cwd":disk_receipt(cwd),"tmp":disk_receipt("/tmp")},
        "git":{"root":run_text(["git","rev-parse","--show-toplevel"],cwd=cwd),"head":run_text(["git","rev-parse","HEAD"],cwd=cwd),"branch":run_text(["git","branch","--show-current"],cwd=cwd),"status_porcelain":run_text(["git","status","--porcelain"],cwd=cwd)},
        "environment":{key:os.environ.get(key) for key in ("HERMES_HOME","PYTHONPATH","TMPDIR","SQLITE_TMPDIR") if key in os.environ},
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--out",default=str(HERE/"out"/"vm-gate")); parser.add_argument("--quick",action="store_true"); parser.add_argument("--skip-tests",action="store_true"); args=parser.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); (out/"receipt.json").write_text(json.dumps(machine_receipt(),indent=2,ensure_ascii=False),encoding="utf-8")
    if not args.skip_tests:
        proc=subprocess.run([sys.executable,"-m","unittest","discover","-s",str(HERE/"tests"),"-v"],cwd=HERE,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT); (out/"tests.txt").write_text(proc.stdout,encoding="utf-8"); print(proc.stdout,end="")
        if proc.returncode: raise SystemExit(proc.returncode)
    if args.quick:
        small_values=(3,10,30); repeats=2; full_values=(5,15,30); budgets=(64,256,1000,5000,10000,20000); fillers=(0,3000,20000); lifecycle_calls=4
    else:
        small_values=(3,5,10,20,30,50,100,300); repeats=5; full_values=(5,10,15,20,25,30); budgets=(64,128,256,512,1000,2000,5000,10000,20000); fillers=(0,20000,250000); lifecycle_calls=8
    jobs=[("small_c.csv",lambda:small_c_sweep(repeats=repeats,values=small_values)),("full_consume.csv",lambda:full_consume_sweep(repeats=repeats,values=full_values)),("budget_pathological.csv",lambda:budget_sweep(repeats=max(1,repeats//2),budgets=budgets)),("db_size.csv",lambda:scale_sweep(repeats=repeats,fillers=fillers)),("lifecycle.csv",lambda:lifecycle_probe(calls=lifecycle_calls)),("nonwal_delete.csv",lambda:nonwal_fallback_probe(repeats=repeats)),("temp_store.csv",lambda:temp_store_sweep(repeats=repeats))]
    counts={}
    for filename,fn in jobs:
        print(f"==> {filename}",flush=True); rows=fn(); write_csv(out/filename,rows); counts[filename]=len(rows)
    print("==> eqp",flush=True); eqp=eqp_probe(filler=20_000 if args.quick else 250_000); (out/"eqp.json").write_text(json.dumps(eqp,indent=2,ensure_ascii=False),encoding="utf-8")
    if eqp["full_child_scan"] or eqp["suspicious_sessions_scan"]: raise SystemExit(f"EQP gate failed: {eqp['bad_details']}")
    (out/"suite_meta.json").write_text(json.dumps({"quick":args.quick,"row_counts":counts,"production_db_opened":False},indent=2),encoding="utf-8"); print(f"wrote {out}")


if __name__=="__main__": main()
