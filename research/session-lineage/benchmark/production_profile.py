#!/usr/bin/env python3
"""Read-only WSL profiler for the frozen recovered production corpus.

Safety policy follows recovery #20/#22: verify the authoritative SHA, reject live
SQLite sidecars, open mode=ro+immutable+query_only, perform no mutations, and verify
the file identity/hash again after profiling. This collects static topology only;
it does not invent ranked search candidates from arbitrary query text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

AUTHORITATIVE_PATH=Path("/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db")
AUTHORITATIVE_SHA256="23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104"
AUTHORITATIVE_COUNTS={"sessions":7268,"messages":231513,"gateway_routing":78}


def sha256_file(path:Path,chunk=8*1024*1024):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block=handle.read(chunk)
            if not block: break
            digest.update(block)
    return digest.hexdigest()


def file_identity(path:Path):
    st=path.stat(); return {"size":st.st_size,"mtime_ns":st.st_mtime_ns,"inode":st.st_ino,"device":st.st_dev,"mode":stat.S_IMODE(st.st_mode)}


def percentile(sorted_values,p):
    if not sorted_values: return None
    pos=(len(sorted_values)-1)*p; lo=int(pos); hi=min(lo+1,len(sorted_values)-1); frac=pos-lo
    return sorted_values[lo]*(1-frac)+sorted_values[hi]*frac


def numeric_summary(values):
    ordered=sorted(values)
    if not ordered: return {"count":0,"min":None,"p50":None,"p95":None,"p99":None,"max":None}
    return {"count":len(ordered),"min":ordered[0],"p50":percentile(ordered,.50),"p95":percentile(ordered,.95),"p99":percentile(ordered,.99),"max":ordered[-1]}


def bucket_depths(depths):
    buckets=Counter()
    for d in depths:
        if d==0:key="0"
        elif d==1:key="1"
        elif d<=3:key="2-3"
        elif d<=5:key="4-5"
        elif d<=10:key="6-10"
        elif d<=32:key="11-32"
        elif d<=64:key="33-64"
        elif d<=256:key="65-256"
        else:key=">256"
        buckets[key]+=1
    return dict(buckets)


def safe_json(text):
    try:
        value=json.loads(text or "{}"); return value if isinstance(value,dict) else {}
    except Exception:return None


def sidecar_receipt(path):
    result={}
    for suffix in ("-wal","-shm","-journal"):
        side=Path(str(path)+suffix); result[suffix]={"exists":side.exists(),"size":side.stat().st_size if side.exists() else 0}
    return result


def enforce_safe_source(path,expected_sha):
    if path.is_symlink(): raise SystemExit(f"REFUSE: database path is a symlink: {path}")
    if not path.is_file(): raise SystemExit(f"REFUSE: database is not a regular file: {path}")
    sidecars=sidecar_receipt(path); dirty={k:v for k,v in sidecars.items() if v["exists"] and v["size"]>0}
    if dirty: raise SystemExit(f"REFUSE: frozen source has non-empty SQLite sidecars: {dirty}")
    actual=sha256_file(path)
    if actual.lower()!=expected_sha.lower(): raise SystemExit(f"REFUSE: SHA-256 mismatch\n expected={expected_sha}\n actual={actual}\n path={path}")
    return actual,sidecars


def open_immutable(path):
    uri="file:"+quote(str(path.resolve()),safe="/:\\")+"?mode=ro&immutable=1"; conn=sqlite3.connect(uri,uri=True,isolation_level=None); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA query_only=ON"); return conn


def table_columns(conn,table): return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def load_session_rows(conn):
    columns=set(table_columns(conn,"sessions")); wanted=[c for c in ("id","parent_session_id","end_reason","model_config","source","started_at","ended_at") if c in columns]
    if "id" not in wanted: raise RuntimeError("sessions.id missing")
    return wanted,[dict(r) for r in conn.execute("SELECT "+",".join(wanted)+" FROM sessions")]


def topology_profile(rows,columns):
    by_id={str(row["id"]):row for row in rows}; has_parent="parent_session_id" in columns; has_end_reason="end_reason" in columns; has_model_config="model_config" in columns; has_source="source" in columns
    generic_parent_edges=0; missing_parent_edges=0; invalid_json=0; markers=Counter(); parent_end_reasons=Counter(); positive_parent={}
    for sid,row in by_id.items():
        parent_id=row.get("parent_session_id") if has_parent else None; config=safe_json(row.get("model_config")) if has_model_config else {}
        if config is None: invalid_json+=1; config={}
        branched=config.get("_branched_from") is not None; delegated=config.get("_delegate_from") is not None; is_tool=str(row.get("source") or "")=="tool" if has_source else False
        if branched:markers["branch_marker"]+=1
        if delegated:markers["delegate_marker"]+=1
        if is_tool:markers["tool_source"]+=1
        if not parent_id:continue
        generic_parent_edges+=1; parent=by_id.get(str(parent_id))
        if parent is None:missing_parent_edges+=1; continue
        parent_reason=str(parent.get("end_reason") or "") if has_end_reason else "<column-missing>"; parent_end_reasons[parent_reason]+=1
        if has_end_reason and parent_reason=="compression" and not branched and not delegated and not is_tool: positive_parent[sid]=str(parent_id)

    memo={}; cycles=set()
    def resolve(start):
        if start in memo:return memo[start]
        path=[]; index={}; cur=start
        while True:
            if cur in memo:
                root,base_depth=memo[cur]
                if root is None:
                    for node in path:memo[node]=(None,None)
                else:
                    for offset,node in enumerate(reversed(path),1):memo[node]=(root,base_depth+offset)
                return memo[start]
            if cur in index:
                cycles.update(path[index[cur]:])
                for node in path:memo[node]=(None,None)
                return (None,None)
            index[cur]=len(path); path.append(cur); parent=positive_parent.get(cur)
            if parent is None:
                memo[cur]=(cur,0)
                for offset,node in enumerate(reversed(path[:-1]),1):memo[node]=(cur,offset)
                return memo[start]
            cur=parent
    for sid in by_id:resolve(sid)
    depths=[depth for root,depth in memo.values() if root is not None and depth is not None]; lineage_sizes=Counter(root for root,depth in memo.values() if root is not None); sizes=sorted(lineage_sizes.values())
    return {"session_rows":len(rows),"generic_parent_edges":generic_parent_edges,"missing_parent_edges":missing_parent_edges,"positive_compression_edges":len(positive_parent),"positive_compression_edge_fraction_of_sessions":len(positive_parent)/len(rows) if rows else 0,"positive_compression_edge_fraction_of_parent_edges":len(positive_parent)/generic_parent_edges if generic_parent_edges else 0,"invalid_model_config_json":invalid_json,"child_markers":dict(markers),"generic_parent_parent_end_reason_counts":dict(parent_end_reasons),"positive_lineage_cycles":len(cycles),"depth":numeric_summary(depths),"depth_buckets":bucket_depths(depths),"lineage_size":numeric_summary(sizes),"lineage_count":len(lineage_sizes),"largest_lineage_sizes":sorted(sizes,reverse=True)[:20],"sessions_in_multi_session_compression_lineage":sum(size for size in sizes if size>1),"all_sessions_naive_depth_work":sum(d+1 for d in depths)}


def profile_database(path,expected_sha,*,enforce_authoritative_counts=False):
    path=Path(path); before_identity=file_identity(path); actual_sha,sidecars=enforce_safe_source(path,expected_sha); started=time.perf_counter(); conn=open_immutable(path)
    try:
        schema=[dict(r) for r in conn.execute("SELECT type,name,tbl_name FROM sqlite_schema ORDER BY type,name")]; tables={r["name"] for r in schema if r["type"]=="table"}; counts={}
        for table in ("sessions","messages","gateway_routing"): counts[table]=int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if table in tables else None
        if enforce_authoritative_counts:
            bad={k:(AUTHORITATIVE_COUNTS[k],counts.get(k)) for k in AUTHORITATIVE_COUNTS if counts.get(k)!=AUTHORITATIVE_COUNTS[k]}
            if bad: raise RuntimeError(f"authoritative canonical row-count mismatch: {bad}")
        quick=[r[0] for r in conn.execute("PRAGMA quick_check")]; fk_violations=sum(1 for _ in conn.execute("PRAGMA foreign_key_check")); wanted,session_rows=load_session_rows(conn); topology=topology_profile(session_rows,set(wanted)); fts_schema=[r["name"] for r in schema if "fts" in str(r["name"]).lower()]
        db_meta={"page_count":int(conn.execute("PRAGMA page_count").fetchone()[0]),"page_size":int(conn.execute("PRAGMA page_size").fetchone()[0]),"freelist_count":int(conn.execute("PRAGMA freelist_count").fetchone()[0]),"journal_mode_observed":str(conn.execute("PRAGMA journal_mode").fetchone()[0]),"schema_version":int(conn.execute("PRAGMA schema_version").fetchone()[0]),"user_version":int(conn.execute("PRAGMA user_version").fetchone()[0])}
    finally:conn.close()
    after_identity=file_identity(path); after_sha=sha256_file(path)
    if before_identity!=after_identity or after_sha!=actual_sha: raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during read-only profiling")
    return {"source":{"path":str(path),"sha256":actual_sha,"identity":before_identity,"sidecars":sidecars,"opened_mode":"ro+immutable+query_only","mutations_performed":False},"runtime":{"python":os.sys.version,"sqlite":sqlite3.sqlite_version},"elapsed_s":time.perf_counter()-started,"canonical_counts":counts,"quick_check":quick[:20],"foreign_key_violations":fk_violations,"db_meta":db_meta,"session_columns_used":wanted,"fts_schema_object_names":fts_schema,"topology":topology,"query_distribution":{"measured":False,"reason":"Static DB topology cannot reconstruct ranked post-search candidate C/K/Kth-root distributions without choosing queries. Collect from real session_search telemetry or safe candidate replay later."}}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--db",type=Path,default=AUTHORITATIVE_PATH); parser.add_argument("--expected-sha",default=AUTHORITATIVE_SHA256); parser.add_argument("--out",type=Path,default=Path("/tmp/hermes-production-lineage-profile")); args=parser.parse_args()
    enforce_counts=(args.db.resolve()==AUTHORITATIVE_PATH.resolve() and args.expected_sha.lower()==AUTHORITATIVE_SHA256); report=profile_database(args.db,args.expected_sha,enforce_authoritative_counts=enforce_counts); args.out.mkdir(parents=True,exist_ok=True); out=args.out/"production_profile.json"; out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8"); print(f"wrote {out}"); print(json.dumps({"sha256":report["source"]["sha256"],"counts":report["canonical_counts"],"depth":report["topology"]["depth"],"lineage_size":report["topology"]["lineage_size"],"positive_compression_edges":report["topology"]["positive_compression_edges"],"query_distribution_measured":False},indent=2,ensure_ascii=False))


if __name__=="__main__":main()
