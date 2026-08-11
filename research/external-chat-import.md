# Research: `external-chat-import`

## 摘要

`data-integration/external-chat-import` 是 Hermes 專用的資料匯入研究／實作方法，目標是把 ChatGPT、Claude、Gemini 等外部對話匯出資料匯入 Hermes 的 `session.db`。

它涵蓋：

- 分析外部聊天 export ZIP、分片 JSON、附件與 manifest。
- 解析 ChatGPT 的樹狀 `mapping`，沿著 `current_node` 與 `parent` 還原線性對話。
- 將 conversation 與 message 映射到 Hermes 的 `sessions`、`messages` schema。
- 產生 Hermes 格式的 session ID。
- 大檔案分批處理、重複匯入檢查、附件處理與時區處理。
- bulk insert 後重建 SQLite FTS，並以搜尋與 spot-check 驗證結果。

### 依賴與邊界

這不是一般化的資料整合技能。內容直接依賴：

- Hermes `session.db`
- Hermes `sessions` / `messages` schema
- Hermes `session_search`
- Hermes 的 FTS tables
- Hermes session ID 格式

因此目前判定為 Hermes-only，不進跨 Hermes/Codex 的共用層。

### 研究狀態

- **已驗證**：完整原文直接取自 `hermes:~/.hermes/skills/data-integration/external-chat-import/SKILL.md`。
- **尚未驗證**：本筆記沒有執行實際外部聊天 export 匯入，也沒有確認目前 Hermes source code 的 schema 是否仍與原文完全一致。
- **下一步**：若要實作，應先以目前 Hermes source code 的 schema 與去敏感化 export fixture 重新驗證欄位、FTS、分支 flattening 與 duplicate detection 假設。

## 原文（完整 `SKILL.md`）

---
name: external-chat-import
description: "Methodology for importing external chat/conversation data (ChatGPT, Claude, Gemini etc.) into Hermes session DB. Includes general data-investigation methodology for unfamiliar formats, schema mapping, tree flattening, bulk insert, and FTS rebuild. Use when importing chat data from ChatGPT, Claude, or Gemini into Hermes session DB."
version: 1.0.0
author: Hermes Agent (skywind5487)
license: MIT
metadata:
  hermes:
    tags: [chatgpt, import, migration, session-db, data-integration, format-analysis]
    related_skills: [data-integration-development, hermes-agent]
triggers:
  - user says "搬遷gpt" "匯入chatgpt" "chatgpt到hermes" "chatgpt匯出"
  - user wants to import external chat data into session DB
  - user asks about session.db schema or bulk insert
---

# External Chat Import — Hermes Session DB Integration

## Overview

Methodology for importing external chat/assistant conversation data into Hermes session DB. Designed for ChatGPT exports but applicable to Claude, Gemini, and other platforms with structured conversation exports.

## When to Use

- User has a ChatGPT/Claude/Gemini data export ZIP/archive
- User wants old conversations to be searchable via `session_search`
- User asks about session.db schema and how external data maps to it
- User wants to merge external data without starting from scratch

**Don't use for:** Export format analysis is already done; skip to implementation. Single-session migration tasks.

## General Data Investigation Methodology

The import pipeline below assumes you've already understood the source format. When the source format is **unfamiliar** (no pre-existing skill/institutional knowledge), apply this methodology FIRST before designing any mapping:

### Core Principle

> Schema definitions tell you what was **intended**. Actual data tells you what **is**.
> Column names tell you what the designer **wanted** to store. NULL ratios and value distributions tell you what the code actually **writes**.

### Step-by-Step

1. **Extract a real sample** — `unzip -o archive.zip file-of-interest.json -d /tmp/`, then `read_file` into context
2. **Pick multiple examples** — normal case + edge case (nulls) + special variant (GPT vs normal, branching vs linear)
3. **Build a type tree diagram** — full nesting with optional types, conditional fields, and notes:
   ```
   Conversation {
     conversation_id: UUID
     current_node: UUID            ← 🎯 Points to last message in tree
     mapping: {                    ← 🎯 THIS IS A TREE, not a list
       node_id: {
         message: null | {         ← 🎯 null = conversation root
           author: {role: "user"|"assistant"|"tool"}
         }
       }
     }
   }
   ```
4. **For DB schemas — go beyond CREATE TABLE**: check NULL ratios, semantic drift by source, value distributions
5. **Schema vs Reality gap analysis**: which columns are dead? which have semantic drift? which names are misleading?

### Remember

- Python JSON summaries collapse arrays, truncate strings, hide nulls, and never show nesting depth
- `read_file` is the only way to see every bracket, every null, every type
- Never write an import script before extracting and reading at least 2 examples
- All dead columns must be identified before designing any mapping

## Pipeline

### Phase 1: Export Format Analysis

Before designing any import, understand the source format:

1. **Extract and list** — `unzip -l` to see all files and estimate total unpacked size
2. **Identify core data files** — conversations JSON, message content files, manifest
3. **Examine schema** — pick one small JSON file, look at structure:
   - Is it list of conversations or single dict?
   - How are messages organized? (flat list vs tree `mapping`)
   - What fields exist? (id, title, timestamps, model, role, content)
4. **Check for attachments** — binary files, file-format mapping
5. **Check manifest** — file size, sharding, version info

**ChatGPT-specific findings (v2026-06):**
- Format: 55 `conversations-000~054.json` chunks (no `message_v2.json`)
- Each conversation has `mapping` (dict of message nodes), `title`, `create_time`, `default_model_slug`
- Message structure: `{author: {role}, content: {content_type, parts: [...]}, create_time, id}`
- Attachments: 2000+ `file_*.dat` files (PNG/PDF/code), mapped via `conversation_asset_file_names.json`
- `chat.html`: 291MB rendered HTML of all conversations
- `export_manifest.json`: complete file listing with logical grouping and sharding info

### Phase 2: Target Schema Analysis

Understand Hermes session.db:

**`sessions` table:**
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | Format: `YYYYMMDD_HHMMSS_random` |
| source | TEXT | e.g. 'discord', 'cron', 'cli' |
| title | TEXT | Session title |
| started_at | REAL | Unix timestamp |
| ended_at | REAL | Unix timestamp |
| model | TEXT | Model name |
| message_count | INTEGER | Denormalized count |

**`messages` table:**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK AUTO | Auto-increment |
| session_id | TEXT FK | References sessions.id |
| role | TEXT | 'user', 'assistant', 'tool', 'session_meta' |
| content | TEXT | Message body text |
| timestamp | REAL | Unix timestamp |
| token_count | INTEGER | Optional |
| platform_message_id | TEXT | Optional external ID |

**FTS:** `messages_fts` + `messages_fts_trigram` — need rebuild after bulk insert.

### Phase 3: Merge Mapping

Map source → target fields:

| Source (ChatGPT) | Target (session.db) | Conversion |
|---|---|---|
| conversation_id (UUID) | sessions.id | Generate `YYYYMMDD_HHMMSS_random` |
| title | sessions.title | Direct |
| create_time | sessions.started_at | Direct (Unix epoch) |
| update_time | sessions.ended_at | Direct (Unix epoch) |
| default_model_slug | sessions.model | Direct |
| `"chatgpt-export"` | sessions.source | Constant |
| message.author.role | messages.role | user→user, assistant→assistant, tool→tool |
| message.content.parts[0] (text) | messages.content | Flatten parts array to text |
| message.create_time | messages.timestamp | Direct (Unix epoch) |
| message.id | messages.platform_message_id | Direct |

### Phase 4: Tree Flattening

ChatGPT stores conversations as a **tree** (`mapping` dict with node `parent`). Must flatten to linear thread:

1. Read `mapping` dict: `{node_id: {id, parent, message}}`
2. Start from `current_node` (the last message in the thread)
3. Follow `parent` backwards until reaching a root node (no parent)
4. Reverse the list to get chronological order
5. Filter out nodes without messages (merge nodes, special nodes)
6. Each message node → one row in `messages`

### Phase 5: Implementation

```python
def chatgpt_to_session(session_id: str, conv: dict) -> tuple[dict, list[dict]]:
    """Convert one ChatGPT conversation → (session_row, [message_rows])."""
    mapping = conv["mapping"]
    current = conv.get("current_node")
    nodes = []
    while current and current in mapping:
        node = mapping[current]
        if node.get("message"):
            nodes.append(node)
        current = node.get("parent")
    nodes.reverse()  # chronological
    
    session_row = {
        "id": session_id,
        "source": "chatgpt-export",
        "title": conv.get("title", ""),
        "started_at": conv.get("create_time"),
        "ended_at": conv.get("update_time"),
        "model": conv.get("default_model_slug", ""),
        "message_count": len(nodes),
    }
    
    messages = []
    for node in nodes:
        msg = node["message"]
        content = ""
        parts = msg.get("content", {}).get("parts", [])
        if parts and isinstance(parts[0], str):
            content = parts[0]
        
        messages.append({
            "session_id": session_id,
            "role": msg["author"]["role"],
            "content": content,
            "timestamp": msg["create_time"],
            "platform_message_id": msg.get("id"),
        })
    
    return session_row, messages
```

### Phase 6: Verification

After import:
1. Count sessions: `SELECT COUNT(*) FROM sessions WHERE source='chatgpt-export'`
2. Count messages: `SELECT COUNT(*) FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE source='chatgpt-export')`
3. Test search: `session_search(query="a keyword from a known conversation")`
4. Spot-check a specific conversation: verify timestamps, roles, content
5. Check FTS: confirm full-text search returns expected results

## Pitfalls

1. **Session ID format** — ChatGPT uses UUIDs (`003a8be5-...`), session.db uses `YYYYMMDD_HHMMSS_random`. Generate from create_time + truncated UUID.
2. **Tree vs linear** — ChatGPT's `mapping` is a tree. Not all conversations are single-threaded. Follow `current_node` up `parent` chain.
3. **Timezone** — ChatGPT timestamps are UTC epoch. session.db also uses UTC epoch (no conversion needed when writing, but display needs UTC+8).
4. **Large files** — ChatGPT export can be 1.5+ GB unpacked. Process chunk-by-chunk, don't load all into memory.
5. **Attachments (.dat files)** — Binary files can't go into `messages.content`. Options: (a) skip, (b) reference by path, (c) add separate table.
6. **FTS rebuild** — After bulk INSERT, rebuild FTS index: `INSERT INTO messages_fts(messages_fts) VALUES('rebuild')`.
7. **Missing fields** — `ended_at` may be null for incomplete sessions. `token_count` is always null (ChatGPT doesn't expose it per-message in export).
8. **Duplicate detection** — Before inserting, check if `platform_message_id` already exists to avoid duplicates.

## Cross-References

- `data-integration-development` — Software development methodology for building data integration systems (complementary, not overlapping)
- `hermes-agent` — Hermes agent configuration and session DB management (protected skill)

