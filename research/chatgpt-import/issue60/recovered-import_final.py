#!/usr/bin/env python3
"""Final import: fix DB gaps + import remaining 793 conversations + verify"""
import sqlite3, json, os, sys, time
from collections import Counter
from datetime import datetime

DB = os.path.expanduser("~/.hermes/state.db")
ZIP_DIR = "/tmp/chatgpt-export"
BATCH_SIZE = 10

def get_conn():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    return conn

def load_all():
    all_convs = []
    chunk_files = sorted(f for f in os.listdir(ZIP_DIR) if f.startswith("conversations-") and f.endswith(".json"))
    for cf in chunk_files:
        with open(os.path.join(ZIP_DIR, cf)) as f:
            all_convs.extend(json.load(f))
    return all_convs

def extract_messages(conv):
    mapping = conv.get("mapping", {})
    current_node_id = conv.get("current_node")
    messages = []
    node_id = current_node_id
    seen = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = mapping.get(node_id)
        if not node: break
        msg = node.get("message")
        if msg:
            role = msg.get("author", {}).get("role", "unknown")
            content_obj = msg.get("content", {})
            content_type = content_obj.get("content_type", "text")
            text_parts = []
            parts = content_obj.get("parts", [])
            reasoning = None
            reasoning_content = None
            for p in parts:
                if isinstance(p, str):
                    text_parts.append(p)
            if content_type == "reasoning_recap":
                reasoning_content = content_obj.get("content", "")
            if content_type == "thoughts":
                thoughts = content_obj.get("thoughts", [])
                if thoughts:
                    reasoning = "\n".join(t.get("content","") for t in thoughts if t.get("content"))
            text = "\n".join(text_parts)
            messages.append({
                "role": "user" if role == "role" else role,
                "content": text,
                "timestamp": msg.get("create_time", 0),
                "platform_message_id": msg.get("id", ""),
                "finish_reason": msg.get("metadata", {}).get("model_slug", ""),
                "reasoning": reasoning,
                "reasoning_content": reasoning_content,
            })
        node_id = node.get("parent")
    messages.reverse()
    return messages

def build_conv_map(all_convs):
    """Build: conversation_id → conversation"""
    return {c.get("conversation_id",""): c for c in all_convs if c.get("conversation_id")}

def fix_missing_conv_id(conn, all_convs):
    """Fix sessions that lack chatgpt_conversation_id in model_config"""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, user_id
        FROM sessions WHERE source='chatgpt-export'
        AND (json_extract(model_config, '$.chatgpt_conversation_id') IS NULL
             OR json_extract(model_config, '$.chatgpt_conversation_id') = '')
    """)
    rows = cur.fetchall()
    if not rows:
        print("  No missing conv_ids to fix")
        return 0
    
    conv_map = build_conv_map(all_convs)
    fixed = 0
    for sid, title, user_id in rows:
        # user_id might contain the conv_id
        conv = conv_map.get(user_id) if user_id else None
        if not conv:
            # Try partial match (user_id might be truncated)
            for cid, c in conv_map.items():
                if cid.startswith(user_id) if user_id else False:
                    conv = c
                    break
        if conv:
            cid = conv.get("conversation_id", "")
            if cid:
                cur.execute("""
                    UPDATE sessions SET model_config = json_set(
                        COALESCE(model_config, '{}'),
                        '$.chatgpt_conversation_id', ?
                    ) WHERE id = ?
                """, (cid, sid))
                fixed += 1
                print(f"  Fixed: {sid} → conv_id={cid[:16]}...")
        else:
            print(f"  CANNOT FIX: {sid}, user_id={user_id}")
    conn.commit()
    return fixed

def import_missing(conn, all_convs, existing_ids, dry_run=False):
    """Import missing conversations in batches"""
    conv_map = build_conv_map(all_convs)
    
    missing = []
    for c in all_convs:
        cid = c.get("conversation_id", "")
        if cid and cid not in existing_ids:
            missing.append(c)
    
    print(f"\n  To import: {len(missing)} conversations")
    if dry_run:
        print(f"  DRY RUN — no data written")
        return 0, 0
    
    imported = 0
    total_msgs = 0
    batch_num = 0
    t_start = time.time()
    
    for i in range(0, len(missing), BATCH_SIZE):
        batch = missing[i:i+BATCH_SIZE]
        b_start = time.time()
        batch_imported = 0
        batch_msgs = 0
        
        for conv in batch:
            cid = conv.get("conversation_id", "")
            title = conv.get("title", "")
            create_time = conv.get("create_time", 0)
            update_time = conv.get("update_time", 0)
            model = conv.get("default_model_slug", "")
            template_id = conv.get("conversation_template_id", "")
            
            model_config = {"chatgpt_conversation_id": cid}
            if template_id:
                model_config["chatgpt_gpt_id"] = template_id
            for flag in ["is_archived", "is_starred"]:
                v = conv.get(flag)
                if v is not None:
                    model_config[f"chatgpt_{flag}"] = v
            
            messages = extract_messages(conv)
            
            date_part = datetime.fromtimestamp(create_time).strftime("%Y%m%d_%H%M%S") if create_time else "unknown"
            session_id = f"{date_part}_{cid[:8]}"
            
            try:
                conn.execute("""
                    INSERT INTO sessions (id, source, user_id, model, model_config,
                                         started_at, ended_at, message_count, title)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (session_id, "chatgpt-export-new", cid, model,
                      json.dumps(model_config), create_time, update_time,
                      len(messages), title))
            except sqlite3.IntegrityError as e:
                # Already exists — update to chatgpt-export
                conn.execute("UPDATE sessions SET source='chatgpt-export' WHERE id=?", (session_id,))
                continue
            
            msg_count = 0
            for m in messages:
                try:
                    conn.execute("""
                        INSERT INTO messages (session_id, role, content, timestamp,
                                             finish_reason, platform_message_id,
                                             reasoning, reasoning_content)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (session_id, m["role"], m["content"], m["timestamp"],
                          m["finish_reason"], m["platform_message_id"],
                          m["reasoning"], m["reasoning_content"]))
                    msg_count += 1
                except sqlite3.IntegrityError:
                    pass
            
            existing_ids.add(cid)
            batch_imported += 1
            batch_msgs += msg_count
        
        conn.commit()
        b_elapsed = time.time() - b_start
        batch_num += 1
        imported += batch_imported
        total_msgs += batch_msgs
        
        print(f"    batch {batch_num:3d}: {batch_imported:3d} sessions, {batch_msgs:4d} msgs, {b_elapsed:.2f}s")
    
    total_time = time.time() - t_start
    print(f"\n  Import complete: {imported} sessions, {total_msgs} msgs in {total_time:.1f}s")
    print(f"  Rate: {imported/total_time:.0f} sessions/s" if total_time > 0 else "")
    return imported, total_msgs

def verify(conn):
    """Final verification"""
    print("\n" + "=" * 70)
    print("FINAL VERIFICATION")
    print("=" * 70)
    
    cur = conn.cursor()
    
    # Count
    cur.execute("SELECT count(*) FROM sessions WHERE source IN ('chatgpt-export', 'chatgpt-export-new')")
    total_sessions = cur.fetchone()[0]
    
    cur.execute("""
        SELECT count(*) FROM messages m 
        JOIN sessions s ON m.session_id = s.id
        WHERE s.source IN ('chatgpt-export', 'chatgpt-export-new')
    """)
    total_msgs = cur.fetchone()[0]
    
    print(f"\n  Sessions: {total_sessions}")
    print(f"  Messages: {total_msgs}")
    
    # Duplicate check
    cur.execute("""
        SELECT json_extract(model_config, '$.chatgpt_conversation_id'), count(*) as c
        FROM sessions WHERE source IN ('chatgpt-export', 'chatgpt-export-new')
        AND json_extract(model_config, '$.chatgpt_conversation_id') IS NOT NULL
        GROUP BY 1 HAVING c > 1
    """)
    dupes = cur.fetchall()
    print(f"  Duplicate conv_ids: {len(dupes)}")
    if dupes:
        for cid, cnt in dupes[:5]:
            print(f"    {cid[:20]}... x{cnt}")
    
    # Missing conv_id
    cur.execute("""
        SELECT count(*) FROM sessions WHERE source IN ('chatgpt-export', 'chatgpt-export-new')
        AND json_extract(model_config, '$.chatgpt_conversation_id') IS NULL
    """)
    print(f"  Without conv_id: {cur.fetchone()[0]}")
    
    # Content type verification
    cur.execute("""
        SELECT m.reasoning IS NOT NULL, m.reasoning_content IS NOT NULL, count(*)
        FROM messages m JOIN sessions s ON m.session_id = s.id
        WHERE s.source IN ('chatgpt-export', 'chatgpt-export-new')
        GROUP BY 1, 2
    """)
    print(f"\n  Reasoning distribution:")
    for has_r, has_rc, cnt in cur.fetchall():
        label = []
        if has_r: label.append("reasoning")
        if has_rc: label.append("reasoning_content")
        label_str = "+".join(label) if label else "no reasoning"
        print(f"    {cnt:6d}  {label_str}")
    
    # Content spot-check: find '絆線' in new data
    cur.execute("""
        SELECT COUNT(*) FROM messages m 
        JOIN sessions s ON m.session_id = s.id
        WHERE s.source='chatgpt-export-new' AND m.content LIKE '%絆線%'
    """)
    print(f"\n  '絆線' in new data: {cur.fetchone()[0]}")
    
    # Final source breakdown
    cur.execute("SELECT source, count(*) FROM sessions GROUP BY source ORDER BY count(*) DESC")
    print(f"\n  Source breakdown:")
    for src, cnt in cur.fetchall():
        print(f"    {src:25s} {cnt:5d}")
    
    return total_sessions, total_msgs


if __name__ == "__main__":
    print("=" * 70)
    print("PHASE 1: FIX EXISTING DB GAPS")
    print("=" * 70)
    
    all_convs = load_all()
    print(f"  Loaded {len(all_convs)} conversations from export")
    
    conn = get_conn()
    fixed = fix_missing_conv_id(conn, all_convs)
    
    # Build existing set
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT json_extract(model_config, '$.chatgpt_conversation_id')
        FROM sessions WHERE source IN ('chatgpt-export', 'chatgpt-export-new')
        AND json_extract(model_config, '$.chatgpt_conversation_id') IS NOT NULL
        AND json_extract(model_config, '$.chatgpt_conversation_id') != ''
    """)
    existing_ids = set(row[0] for row in cur.fetchall())
    print(f"  Existing conv_ids: {len(existing_ids)}")
    
    # Phase 2: Import missing
    print("\n" + "=" * 70)
    print("PHASE 2: IMPORT MISSING CONVERSATIONS")
    print("=" * 70)
    
    imp, msgs = import_missing(conn, all_convs, existing_ids)
    
    # Phase 3: Update source to 'chatgpt-export' for all new ones
    conn.execute("UPDATE sessions SET source='chatgpt-export' WHERE source='chatgpt-export-new'")
    conn.commit()
    
    # Phase 4: Verify
    v = verify(conn)
    
    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    
    conn.close()
