#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,math,re,sqlite3,statistics,time
from pathlib import Path
from scenarios import ScenarioFile,make_perf_scenarios,reference
from temp_memo import pure_temp
from fixed3_optimized import fixed3_shared_cross

def pct(v,p):
    s=sorted(v); x=(len(s)-1)*p; lo=math.floor(x); hi=math.ceil(x)
    return s[lo] if lo==hi else s[lo]*(hi-x)+s[hi]*(x-lo)

def stats(v):
    return dict(mean_ms=statistics.fmean(v),median_ms=statistics.median(v),p95_ms=pct(v,.95),stdev_ms=statistics.pstdev(v),min_ms=min(v),max_ms=max(v))

def one(conn,s,fn,B):
    db=type('D',(),{'conn':conn,'scenario':s})(); ref=reference(conn,s.k)
    t=time.perf_counter_ns(); r=fn(db,s.k,B); return (time.perf_counter_ns()-t)/1e6,r,ref

def run_scenario(s,filler,warm,cold,B):
    sf=ScenarioFile(s,filler); rows=[]; algs=[('pure_temp',pure_temp),('fixed3_shared_bound',fixed3_shared_cross)]
    try:
        conn=sf.open()
        try:
            for name,fn in algs:
                one(conn,s,fn,B)
                vals=[]
                for _ in range(warm): dt,last,ref=one(conn,s,fn,B); vals.append(dt)
                rows.append({'scenario':s.name,'k':s.k,'candidates':len(s.candidates),'mode':'warm','algorithm':name,**stats(vals),'exact':last['roots']==ref,'work':last['work'],'bound_hit':last['bound_hit'],'statements':last['statements'],'description':s.description})
        finally: conn.close()
        for name,fn in algs:
            vals=[]
            for _ in range(cold):
                sf.fadvise_drop(); conn=sf.open()
                try: dt,last,ref=one(conn,s,fn,B); vals.append(dt)
                finally: conn.close()
            rows.append({'scenario':s.name,'k':s.k,'candidates':len(s.candidates),'mode':'cold_fadvise_reopen','algorithm':name,**stats(vals),'exact':last['roots']==ref,'work':last['work'],'bound_hit':last['bound_hit'],'statements':last['statements'],'description':s.description})
    finally: sf.close()
    return rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',default=str(Path(__file__).resolve().parent/'out'/'final-duel'))
    p.add_argument('--warm',type=int,default=9); p.add_argument('--cold',type=int,default=7)
    p.add_argument('--filler',type=int,default=20000); p.add_argument('--budget',type=int,default=10000)
    p.add_argument('--regex',default=''); p.add_argument('--quick',action='store_true')
    a=p.parse_args()
    if a.quick: a.warm=3; a.cold=2; a.filler=min(a.filler,3000)
    scenarios=[s for s in make_perf_scenarios() if s.performance]
    if a.regex: scenarios=[s for s in scenarios if re.search(a.regex,s.name)]
    rows=[]
    for i,s in enumerate(scenarios,1):
        print(f'[{i}/{len(scenarios)}] {s.name}',flush=True)
        rr=run_scenario(s,a.filler,a.warm,a.cold,a.budget); rows += rr
        for r in rr:
            print(f"  {r['mode']:22s} {r['algorithm']:20s} med={r['median_ms']:.3f} p95={r['p95_ms']:.3f} work={r['work']} exact={r['exact']}")
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    with (out/'final_duel.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    (out/'meta.json').write_text(json.dumps({'sqlite':sqlite3.sqlite_version,'warm':a.warm,'cold':a.cold,'filler':a.filler,'budget':a.budget},indent=2),encoding='utf-8')
    print('wrote',out)

if __name__=='__main__': main()
