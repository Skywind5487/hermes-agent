from __future__ import annotations

import math

FIXED3_SQL = r'''
WITH RECURSIVE
s1_seeds(node) AS (SELECT session_id FROM candidates WHERE ord<=:b1),
s1_nodes(node) AS (
  SELECT node FROM s1_seeds
  UNION
  SELECT p.id FROM s1_nodes n JOIN sessions child ON child.id=n.node JOIN sessions p ON p.id=child.parent_id
  WHERE child.edge_kind='compression' LIMIT :B
),
s1_edges(child,parent) AS (
  SELECT s.id,s.parent_id FROM sessions s JOIN s1_nodes n ON n.node=s.id JOIN s1_nodes p ON p.node=s.parent_id WHERE s.edge_kind='compression'
),
s1_labels(node,root) AS (
  SELECT n.node,n.node FROM s1_nodes n JOIN sessions s ON s.id=n.node WHERE s.edge_kind!='compression' OR s.parent_id IS NULL
),
s1_map(node,root) AS (
  SELECT node,root FROM s1_labels UNION ALL SELECT e.child,m.root FROM s1_map m JOIN s1_edges e ON e.parent=m.node
),
s1_seen(n) AS (
  SELECT COUNT(DISTINCT m.root) FROM candidates c JOIN s1_map m ON m.node=c.session_id WHERE c.ord<=:b1
),
s2_seeds(node) AS (
  SELECT c.session_id FROM candidates c WHERE c.ord>:b1 AND c.ord<=:b12 AND (SELECT n FROM s1_seen)<:K
    AND NOT EXISTS(SELECT 1 FROM s1_map m WHERE m.node=c.session_id)
),
s2_nodes(node) AS (
  SELECT node FROM s2_seeds
  UNION
  SELECT p.id FROM s2_nodes n JOIN sessions child ON child.id=n.node JOIN sessions p ON p.id=child.parent_id
  WHERE child.edge_kind='compression' AND NOT EXISTS(SELECT 1 FROM s1_map m WHERE m.node=p.id)
  LIMIT max(0,:B-(SELECT COUNT(*) FROM s1_nodes))
),
s2_edges(child,parent) AS (
  SELECT s.id,s.parent_id FROM sessions s JOIN s2_nodes n ON n.node=s.id JOIN s2_nodes p ON p.node=s.parent_id WHERE s.edge_kind='compression'
),
s2_labels(node,root) AS (
  SELECT n.node,n.node FROM s2_nodes n CROSS JOIN sessions s WHERE s.id=n.node AND (s.edge_kind!='compression' OR s.parent_id IS NULL)
  UNION
  SELECT child.id,m.root
  FROM s2_nodes n CROSS JOIN sessions child JOIN s1_map m ON m.node=child.parent_id
  WHERE child.id=n.node AND child.edge_kind='compression'
),
s2_map(node,root) AS (
  SELECT node,root FROM s2_labels UNION ALL SELECT e.child,m.root FROM s2_map m JOIN s2_edges e ON e.parent=m.node
),
known12(node,root) AS (SELECT node,root FROM s1_map UNION SELECT node,root FROM s2_map),
s12_seen(n) AS (
  SELECT COUNT(DISTINCT m.root) FROM candidates c JOIN known12 m ON m.node=c.session_id WHERE c.ord<=:b12
),
s3_seeds(node) AS (
  SELECT c.session_id FROM candidates c WHERE c.ord>:b12 AND (SELECT n FROM s12_seen)<:K
    AND NOT EXISTS(SELECT 1 FROM known12 m WHERE m.node=c.session_id)
),
s3_nodes(node) AS (
  SELECT node FROM s3_seeds
  UNION
  SELECT p.id FROM s3_nodes n JOIN sessions child ON child.id=n.node JOIN sessions p ON p.id=child.parent_id
  WHERE child.edge_kind='compression' AND NOT EXISTS(SELECT 1 FROM known12 m WHERE m.node=p.id)
  LIMIT max(0,:B-(SELECT COUNT(*) FROM s1_nodes)-(SELECT COUNT(*) FROM s2_nodes))
),
s3_edges(child,parent) AS (
  SELECT s.id,s.parent_id FROM sessions s JOIN s3_nodes n ON n.node=s.id JOIN s3_nodes p ON p.node=s.parent_id WHERE s.edge_kind='compression'
),
s3_labels(node,root) AS (
  SELECT n.node,n.node FROM s3_nodes n CROSS JOIN sessions s WHERE s.id=n.node AND (s.edge_kind!='compression' OR s.parent_id IS NULL)
  UNION
  SELECT child.id,m.root
  FROM s3_nodes n CROSS JOIN sessions child JOIN known12 m ON m.node=child.parent_id
  WHERE child.id=n.node AND child.edge_kind='compression'
),
s3_map(node,root) AS (
  SELECT node,root FROM s3_labels UNION ALL SELECT e.child,m.root FROM s3_map m JOIN s3_edges e ON e.parent=m.node
),
known123(node,root) AS (SELECT node,root FROM known12 UNION SELECT node,root FROM s3_map),
winners AS (
  SELECT m.root,MIN(c.ord) first_ord FROM candidates c JOIN known123 m ON m.node=c.session_id GROUP BY m.root ORDER BY first_ord LIMIT :K
)
SELECT (SELECT group_concat(root,'|') FROM winners) roots,
  (SELECT count(*) FROM s1_nodes) s1work,(SELECT count(*) FROM s2_nodes) s2work,(SELECT count(*) FROM s3_nodes) s3work,
  (SELECT count(*) FROM s1_seeds) s1seeds,(SELECT count(*) FROM s2_seeds) s2seeds,(SELECT count(*) FROM s3_seeds) s3seeds
'''


def fixed3_params(K: int, B: int) -> dict[str, int]:
    b1 = math.ceil(1.5 * K)
    b2 = math.ceil((1.5 ** 2) * K)
    return {"b1": b1, "b12": b1 + b2, "K": K, "B": B}


def fixed3_shared_cross(db, K: int, B: int):
    row = db.conn.execute(FIXED3_SQL, fixed3_params(K, B)).fetchone()
    roots = [] if not row["roots"] else row["roots"].split("|")
    work = row["s1work"] + row["s2work"] + row["s3work"]
    return {"roots": roots, "work": work, "bound_hit": (work >= B and len(roots) < K), "statements": 1, "candidates": row["s1seeds"] + row["s2seeds"] + row["s3seeds"], "temp_peak_bytes": 0}
