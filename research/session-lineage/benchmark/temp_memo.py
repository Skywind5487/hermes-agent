class TraceCounter:
    def __init__(self,conn): self.conn=conn; self.count=0
    def __enter__(self): self.conn.set_trace_callback(self._cb); return self
    def _cb(self,sql): self.count += 1
    def __exit__(self,*a): self.conn.set_trace_callback(None)

def pure_temp(db,K:int,B:int):
    conn=db.conn
    with TraceCounter(conn) as tc:
        conn.execute('BEGIN')
        try:
            conn.execute('CREATE TEMP TABLE lineage_memo(node TEXT PRIMARY KEY,root TEXT NOT NULL) WITHOUT ROWID')
            roots=[]; rootset=set(); work=0; inspected=0; bound_hit=False
            cursor=conn.execute('SELECT ord,session_id FROM candidates ORDER BY ord')
            for candidate in cursor:
                inspected += 1; cur=candidate['session_id']; path=[]; local=set(); resolved=None
                while True:
                    row=conn.execute('''SELECT s.parent_id,s.edge_kind,m.root cached_root
                        FROM sessions s LEFT JOIN lineage_memo m ON m.node=s.id WHERE s.id=?''',(cur,)).fetchone()
                    if not row: break
                    if row['cached_root'] is not None: resolved=row['cached_root']; break
                    if cur in local: break
                    if work>=B: bound_hit=True; break
                    local.add(cur); path.append(cur); work += 1
                    if row['edge_kind']!='compression' or row['parent_id'] is None: resolved=cur; break
                    cur=row['parent_id']
                if bound_hit: break
                if resolved is not None:
                    conn.executemany('INSERT OR IGNORE INTO lineage_memo(node,root) VALUES(?,?)',[(x,resolved) for x in path])
                    if resolved not in rootset:
                        rootset.add(resolved); roots.append(resolved)
                        if len(roots)>=K: break
            cursor.close(); conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK'); conn.execute('DROP TABLE IF EXISTS lineage_memo'); raise
        conn.execute('DROP TABLE IF EXISTS lineage_memo')
    return {'roots':roots[:K],'work':work,'bound_hit':bound_hit,'statements':tc.count,'candidates':inspected}
