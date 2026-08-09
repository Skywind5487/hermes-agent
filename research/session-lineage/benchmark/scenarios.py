from __future__ import annotations
import os, random, sqlite3, tempfile
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
    def __init__(self): self.sessions=[]; self.n=0
    def new_id(self,p='s'):
        self.n += 1; return f'{p}{self.n:07d}'
    def root(self):
        x=self.new_id(); self.sessions.append((x,None,'root')); return x
    def child(self,parent,kind='compression'):
        x=self.new_id(); self.sessions.append((x,parent,kind)); return x
    def chain(self,depth:int):
        a=[self.root()]
        for _ in range(depth): a.append(self.child(a[-1],'compression'))
        return a

def uniq(xs): return list(dict.fromkeys(xs))

def make_perf_scenarios(C=300):
    O=[]
    b=Builder(); c=[b.root() for _ in range(C)]
    O.append(Scenario('modern_roots_k3',b.sessions,c,3,'300 distinct modern/in-place roots; K immediately'))
    b=Builder(); c=[b.root() for _ in range(C)]
    O.append(Scenario('modern_roots_k10',b.sessions,c,10,'300 distinct modern/in-place roots; K=10 early'))
    for d in (1,3,5):
        b=Builder(); c=[b.chain(d)[-1] for _ in range(C)]
        O.append(Scenario(f'independent_depth{d}_k3',b.sessions,c,3,f'{C} independent legacy chains depth {d}; K early'))
    b=Builder(); chains=[b.chain(5) for _ in range(50)]; c=[]
    for ch in chains: c.extend(reversed(ch))
    O.append(Scenario('blocked_50_lineages_depth5_k3',b.sessions,c[:C],3,'50 depth-5 lineages in ranked blocks; Kth lineage begins at candidate 13'))
    b=Builder(); chains=[b.chain(5) for _ in range(50)]; c=[]
    for level in range(5,-1,-1):
        for ch in chains: c.append(ch[level])
    O.append(Scenario('interleaved_50_lineages_depth5_k3',b.sessions,c[:C],3,'50 depth-5 lineages interleaved; first 3 candidates are different roots'))
    b=Builder(); chains=[b.chain(5) for _ in range(50)]; c=[]
    for ch in chains: c.extend(reversed(ch))
    O.append(Scenario('blocked_50_lineages_depth5_k10',b.sessions,c[:C],10,'50 depth-5 lineages in blocks; K=10 arrives around candidate 55'))
    b=Builder(); chains=[b.chain(5) for _ in range(5)]; c=[]
    for ch in chains: c.extend(reversed(ch))
    O.append(Scenario('five_lineages_k10_unreachable',b.sessions,c,10,'Only 5 lineages / 30 distinct sessions; K=10 unreachable'))
    b=Builder(); a=b.chain(5); d=b.chain(5); c=list(reversed(a))+list(reversed(d)); third=b.chain(5); c.append(third[-1])
    while len(c)<C:
        ch=b.chain(5)
        for x in reversed(ch):
            if len(c)>=C: break
            c.append(x)
    O.append(Scenario('kth_at_13_then_depth5_tail_k3',b.sessions,c[:C],3,'First 12 candidates are exactly 2 lineages; candidate 13 is Kth; realistic depth5 tail follows'))
    b=Builder(); a=b.chain(5); r2=b.chain(5); r3=b.chain(5); c=list(reversed(a))[:5]
    c += list(reversed(r2))[:4] + list(reversed(r3))[:3]
    while len(c)<C:
        ch=b.chain(random.Random(len(c)+SEED).randint(0,5))
        for x in reversed(ch):
            if len(c)>=C: break
            c.append(x)
    O.append(Scenario('speculation_fails_shared_middle_realistic_k3',b.sessions,uniq(c)[:C],3,'TEMP-sized prefix collapses; next shared-sized batch contains two new legacy lineages'))
    rng=random.Random(1001); b=Builder(); c=[]
    while len(c)<C:
        d=0 if rng.random()<0.78 else rng.randint(1,5); ch=b.chain(d); c.append(ch[-1])
        if d and rng.random()<0.35 and len(c)<C: c.append(ch[rng.randrange(len(ch)-1)])
    O.append(Scenario('modern_sparse_legacy_mix_k3',b.sessions,uniq(c)[:C],3,'~78% modern roots, sparse depth1..5 legacy continuations'))
    for sd in (11,22,33):
        rng=random.Random(sd); b=Builder(); chains=[b.chain(rng.randint(0,5)) for _ in range(90)]; pool=[]
        for i,ch in enumerate(chains):
            w=1/(i+1)**1.1
            for node in reversed(ch): pool.append((rng.random()/w,node))
        pool.sort(); c=uniq([x for _,x in pool])[:C]
        O.append(Scenario(f'random_zipf_realistic_{sd}_k3',b.sessions,c,3,'Random clustered realistic depth0..5 candidates, unique session IDs'))
    b=Builder(); root=b.root(); trunk=b.child(root,'compression'); c=[]
    for _ in range(C):
        x=b.child(trunk,'compression')
        if len(c)%2==0: x=b.child(x,'compression')
        c.append(x)
    O.append(Scenario('malformed_fanout_stress_k3',b.sessions,c,3,'Many compression children share one parent; stress/compat topology, not normal modern workload'))
    b=Builder(); ch=b.chain(1000)
    O.append(Scenario('safety_depth1000_bound',b.sessions,[ch[-1]],3,'Pathological acyclic chain for global-bound safety only',False))
    return O

def make_correctness_scenarios():
    O=[]
    b=Builder(); r=b.root(); O.append(Scenario('correct_root',b.sessions,[r],1,'root',False))
    b=Builder(); ch=b.chain(5); O.append(Scenario('correct_compression',b.sessions,[ch[-1]],1,'depth5 compression',False))
    for kind in ('fork','delegation','tool'):
        b=Builder(); p=b.root(); x=b.child(p,kind); y=b.child(x,'compression')
        O.append(Scenario(f'correct_{kind}_boundary',b.sessions,[y],1,f'{kind} starts lineage',False))
    b=Builder(); x=b.new_id(); b.sessions.append((x,'missing','compression')); O.append(Scenario('correct_missing_parent',b.sessions,[x],1,'unresolved missing parent',False))
    b=Builder(); a=b.new_id(); z=b.new_id(); b.sessions.extend([(a,z,'compression'),(z,a,'compression')]); O.append(Scenario('correct_cycle',b.sessions,[a],1,'unresolved cycle',False))
    return O

class ScenarioFile:
    def __init__(self,s:Scenario,filler:int=20000):
        self.s=s; fd,self.path=tempfile.mkstemp(prefix='hermes-lineage-',suffix='.db'); os.close(fd)
        conn=sqlite3.connect(self.path,isolation_level=None)
        conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA synchronous=NORMAL')
        conn.executescript('''
        CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_id TEXT,edge_kind TEXT NOT NULL);
        CREATE INDEX sessions_parent_idx ON sessions(parent_id);
        CREATE TABLE candidates(ord INTEGER PRIMARY KEY,session_id TEXT NOT NULL UNIQUE);
        ''')
        conn.execute('BEGIN'); conn.executemany('INSERT INTO sessions VALUES(?,?,?)',s.sessions)
        filler_rows=[(f'f{((i*2654435761)&0xffffffff):08x}_{i:06d}',None,'root') for i in range(filler)]
        conn.executemany('INSERT INTO sessions VALUES(?,?,?)',filler_rows)
        uc=uniq(s.candidates)
        if len(uc)!=len(s.candidates): raise AssertionError(f'duplicate candidates in {s.name}')
        conn.executemany('INSERT INTO candidates VALUES(?,?)',[(i+1,x) for i,x in enumerate(uc)])
        conn.execute('COMMIT'); conn.execute('PRAGMA wal_checkpoint(TRUNCATE)'); conn.close()
    def fadvise_drop(self):
        if not hasattr(os,'posix_fadvise'): return
        fd=os.open(self.path,os.O_RDONLY)
        try: os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
        finally: os.close(fd)
    def open(self):
        conn=sqlite3.connect(f'file:{self.path}?mode=ro',uri=True,isolation_level=None); conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA temp_store=MEMORY'); conn.execute('PRAGMA cache_size=-32768'); return conn
    def close(self):
        for p in (self.path,self.path+'-wal',self.path+'-shm'):
            try: os.remove(p)
            except FileNotFoundError: pass

def reference(conn,K):
    roots=[]; seen_roots=set()
    for r in conn.execute('SELECT session_id FROM candidates ORDER BY ord'):
        cur=r['session_id']; seen=set(); resolved=None
        while True:
            if cur in seen: resolved=None; break
            seen.add(cur); row=conn.execute('SELECT parent_id,edge_kind FROM sessions WHERE id=?',(cur,)).fetchone()
            if not row: resolved=None; break
            if row['edge_kind']!='compression' or row['parent_id'] is None: resolved=cur; break
            if not conn.execute('SELECT 1 FROM sessions WHERE id=?',(row['parent_id'],)).fetchone(): resolved=None; break
            cur=row['parent_id']
        if resolved is not None and resolved not in seen_roots:
            seen_roots.add(resolved); roots.append(resolved)
            if len(roots)>=K: break
    return roots
