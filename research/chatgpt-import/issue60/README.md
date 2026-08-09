# Issue #60 — ChatGPT → Hermes import provenance

This directory is the durable, public research record for #60.

Full raw historical transcripts are **not committed** because this repository is public and the raw bundle contains unrelated/private historical context. Exact locators and cryptographic hashes are preserved instead.

## Evidence base

Canonical frozen DB:

- path: `/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db`
- SHA-256: `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104`
- opened by the evidence extractor as `mode=ro&immutable=1 + PRAGMA query_only=ON`
- mutations performed: `False`
- counts: sessions=7268, messages=231513, gateway_routing=78

Stage-2 extraction found:

- `chatgpt-export` sessions: 5447
- whole-DB keyword hit rows: 32264
- whole-DB candidate sessions: 2898
- pre-Hermes exact ChatGPT-message match rows: 0
- targeted anomaly exact-match rows: 0

## Corrected historical session boundary

### PROVEN — origin/intake

The migration request begins in `20260616_054313_151a9806` (`ChatGPT数据整合计划`), at message `90307`.

At message `90416` the user changes topic to the oversized ZIP / Windows C: drive investigation. The remainder of that long session must **not** be treated as importer evidence.

### PROVEN — implementation session

The high-value implementation session is `20260616_100300_58622e7c` (`ChatGPT搬遷討論回顧`).

It contains the final exact importer source and exact execution output, recovered into:

- `recovered-import_final.py`
- `import-final-run-output.txt`

See `evidence-locators.md` for precise historical message IDs.

## PROVEN — final importer behavior

The exact recovered script implements:

```text
ChatGPT conversation
    ↓
current_node
    ↓
walk mapping[node].parent backward
    ↓
skip null/non-message nodes
    ↓
reverse
    ↓
one linear active-branch Hermes session
```

### Session field mapping in the final script

| Hermes field | ChatGPT source / derivation | Confidence |
|---|---|---|
| `id` | `datetime(create_time)` + first 8 chars of `conversation_id` | PROVEN |
| `title` | `conversation.title` | PROVEN |
| `started_at` | `conversation.create_time` | PROVEN |
| `ended_at` | `conversation.update_time` | PROVEN |
| `source` | inserted as temporary `chatgpt-export-new`, then normalized to `chatgpt-export` | PROVEN |
| `user_id` | full `conversation_id` | PROVEN |
| `model` | `default_model_slug` | PROVEN |
| `model_config.chatgpt_conversation_id` | full `conversation_id` | PROVEN |
| `model_config.chatgpt_gpt_id` | `conversation_template_id`, when present | PROVEN |
| `model_config.chatgpt_is_archived` | `is_archived`, when present | PROVEN |
| `model_config.chatgpt_is_starred` | `is_starred`, when present | PROVEN |
| `message_count` | number of extracted active-branch message nodes | PROVEN |
| `display_name` | not written by final importer | PROVEN about script behavior |
| `parent_session_id` | not written by final importer | PROVEN about script behavior |
| `end_reason` | not written by final importer | PROVEN about script behavior |
| `cwd` | not written by final importer | PROVEN about script behavior |

“Not written” does not by itself prove the final DB value if later/default behavior could modify it; final-row claims still require DB evidence.

### Message field mapping in the final script

| Hermes field | ChatGPT source / derivation | Confidence |
|---|---|---|
| `session_id` | generated session ID above | PROVEN |
| `role` | `message.author.role` (with the script's literal `role == "role" → "user"` special case) | PROVEN |
| `content` | newline-join of **string** entries in `content.parts` | PROVEN |
| `timestamp` | `message.create_time` | PROVEN |
| `platform_message_id` | ChatGPT message `id` | PROVEN |
| `finish_reason` | `message.metadata.model_slug` (despite the Hermes column name) | PROVEN |
| `reasoning` | joined `thoughts[].content` for `content_type == "thoughts"` | PROVEN |
| `reasoning_content` | `content.content` for `content_type == "reasoning_recap"` | PROVEN |

The final script does not preserve arbitrary non-string `content.parts`, `content_references`, all alternate branches, or every possible tool/content structure.

## PROVEN — merge / dedup behavior

The final script:

1. loads every `conversations-*.json` file under `/tmp/chatgpt-export`;
2. builds an existing set from `model_config.chatgpt_conversation_id`;
3. attempts to repair existing imported sessions missing that ID using `user_id`;
4. imports only conversation IDs absent from that set;
5. commits in batches of 10;
6. catches session `sqlite3.IntegrityError`, marks that generated session ID as `chatgpt-export`, and skips inserting its messages;
7. ignores per-message `sqlite3.IntegrityError`;
8. converts `chatgpt-export-new` to `chatgpt-export` after the import;
9. verifies duplicate/missing conversation IDs and counts.

This is direct SQL into `~/.hermes/state.db`; it is not using a high-level Hermes session-storage API.

## PROVEN — final recorded run

The exact recorded run says:

```text
Loaded conversations:          5,447
Existing conversation IDs:     4,654
Conversations considered missing: 793
New sessions actually imported:   792
New messages inserted:          3,322

Final chatgpt-export sessions:  5,447
Final imported messages:       89,434
Duplicate conversation IDs:     0
Sessions without conv_id:        1
```

The one remaining row without a conversation ID is reported as:

`20231013_125540_0041efa3`

The transcript subsequently investigates that orphan. Do not silently rewrite the historical run output to “all 5,447 had conv_id”; the exact run artifact above says one remained at that point.

## STRONG INFERENCE / historical summary context

The context-compaction handoff in the main implementation session reports earlier exploration including:

- an approximately 892 MB ChatGPT export;
- 55 `conversations-*.json` chunks;
- 5,447 conversations;
- representative/small import tests before the final pass;
- field/content-type audits;
- failed full-import attempts, timeout/WAL investigation, and the temporary false hypothesis that title uniqueness caused dropped rows;
- later schema checks that disproved that title-UNIQUE hypothesis.

These are valuable historical context, but details that exist only in the compaction summary are weaker than the exact recovered final script and terminal results.

## PROVEN negative evidence / OPEN anomaly

Stage 2 found **0** pre-Hermes session rows with any informative exact-content match to the final `chatgpt-export` corpus, and **0** targeted exact-message matches for the anomaly set.

Therefore the simplest theory—

> “the Hermes-shaped pre-Hermes rows are just exact duplicate ChatGPT messages imported again under `discord` / `cli`”

—is not supported by exact-content evidence.

This does **not** explain the historical lineage anomaly. The reason pre-2026-06-16 rows can contain Hermes-shaped `source`, `parent_session_id`, `end_reason='compression'`, etc. remains **OPEN** and should be investigated as a transformation / earlier import / reinsertion / rewrite question.

## Public vs private evidence

Public:
- this research note;
- exact final importer;
- exact final run output;
- precise evidence locators;
- evidence hashes.

Private/local:
- complete stage-1/stage-2 evidence archives;
- full raw historical transcripts.

See `evidence-manifest.json`.
