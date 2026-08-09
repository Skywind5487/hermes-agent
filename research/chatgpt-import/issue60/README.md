# Issue #60 — ChatGPT → Hermes import provenance

This directory is the durable, public research record for #60.

Full raw historical transcripts are **not committed** because this repository is public and the raw bundle contains unrelated/private historical context. Exact locators and cryptographic hashes are preserved instead.

## Corrected timeline — source of truth

**User-confirmed:** Hermes runtime began **2026-05-29**.

**2026-06-16 is the ChatGPT export import/merge date, not Hermes adoption.** The import was performed against an already-populated Hermes `state.db`.

Therefore May-29 through June-15 `discord` / `cli` / `cron` sessions, tool calls, `parent_session_id`, and `end_reason='compression'` are eligible as genuine Hermes-native runtime evidence. Do not treat them as import anomalies merely because they predate June 16.

The May-30 depth-14 lineage used by #54 is inside the Hermes-runtime era.

## Evidence base

Canonical frozen DB:

- path: `/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db`
- SHA-256: `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104`
- opened by the evidence extractor as `mode=ro&immutable=1 + PRAGMA query_only=ON`
- mutations performed: `False`
- counts: sessions=7268, messages=231513, gateway_routing=78

Final `chatgpt-export` corpus: 5,447 sessions.

## Historical migration session boundary

### PROVEN — origin / intake

The migration request begins in `20260616_054313_151a9806` (`ChatGPT数据整合计划`), at message `90307`.

At message `90416` the user changes topic to the oversized ZIP / Windows C: drive investigation. The remainder of that long session must **not** be treated as importer evidence.

### PROVEN — implementation / reconstruction

The high-value implementation session is `20260616_100300_58622e7c` (`ChatGPT搬遷討論回顧`).

It contains the exploration, representative imports, bulk-import failures/corrections, final exact importer, orphan repair, and FTS verification.

See `evidence-locators.md` for precise historical message IDs.

## PROVEN — import merged into an existing Hermes DB

At the representative 57-session ChatGPT checkpoint, the historical transcript records:

```text
discord:        620
cron:           252
cli:             15
chatgpt-export:  57
-------------------
total:          944
```

So **887 non-ChatGPT Hermes sessions were already present** before the bulk ChatGPT migration finished.

The exact final run later reports:

```text
chatgpt-export: 5447
discord:         621
cron:            253
cli:              15
```

This is an existing-DB merge, not an empty-DB import.

## PROVEN — final importer behavior

The exact recovered script is `recovered-import_final.py`, reconstructed from historical message `188425` (`write_file` payload for `/tmp/chatgpt-export/import_final.py`).

It implements:

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

| Hermes field | Final-script source / derivation | Confidence |
|---|---|---|
| `id` | UTC `datetime(create_time)` + first 8 chars of `conversation_id` | PROVEN |
| `title` | `conversation.title` | PROVEN |
| `started_at` | `conversation.create_time` | PROVEN **for the final script** |
| `ended_at` | `conversation.update_time` | PROVEN **for the final script** |
| `source` | temporary `chatgpt-export-new`, then normalized to `chatgpt-export` | PROVEN |
| `user_id` | full `conversation_id` | PROVEN |
| `model` | `default_model_slug` | PROVEN |
| `model_config.chatgpt_conversation_id` | full `conversation_id` | PROVEN |
| `model_config.chatgpt_gpt_id` | `conversation_template_id`, when present | PROVEN |
| `model_config.chatgpt_is_archived` | `is_archived`, when present | PROVEN |
| `model_config.chatgpt_is_starred` | `is_starred`, when present | PROVEN |
| `message_count` | number of extracted active-branch message nodes | PROVEN |
| `display_name` | not written by final importer | PROVEN about final-script behavior |
| `parent_session_id` | not written by final importer | PROVEN about final-script behavior |
| `end_reason` | not written by final importer | PROVEN about final-script behavior |
| `cwd` | not written by final importer | PROVEN about final-script behavior |

**Important:** final-script behavior is not automatically the behavior of every row in the final corpus, because thousands of conversations already existed from earlier test/partial import passes and were deduplicated rather than rewritten by the final pass.

### Message field mapping in the final script

| Hermes field | ChatGPT source / derivation | Confidence |
|---|---|---|
| `session_id` | generated session ID above | PROVEN |
| `role` | `message.author.role` (including the script's literal `role == "role" → "user"` special case) | PROVEN |
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
6. on session `sqlite3.IntegrityError`, executes `UPDATE sessions SET source='chatgpt-export' WHERE id=?` and skips inserting that conversation's messages;
7. ignores per-message `sqlite3.IntegrityError`;
8. converts `chatgpt-export-new` to `chatgpt-export` after the import;
9. verifies duplicate/missing conversation IDs and counts.

This is direct SQL into `~/.hermes/state.db`; it is not using a high-level Hermes session-storage API.

The `IntegrityError → source rewrite` path is now a high-value audit target: determine whether it ever hit a pre-existing Hermes-native row or only an earlier ChatGPT-import row.

## PROVEN — final recorded run and orphan repair

`import-final-run-output.txt` records:

```text
Loaded conversations:              5,447
Existing conversation IDs:         4,654
Conversations considered missing:    793
New sessions actually imported:      792
New messages inserted:              3,322

Final chatgpt-export sessions:      5,447
Final imported messages:           89,434
Duplicate conversation IDs:            0
Sessions without conv_id:              1
```

The remaining v1 orphan was:

`20231013_125540_0041efa3`

The same historical session then recovers its original ChatGPT conversation ID:

`0041efa3-8722-493e-92d3-58c0f9838b23`

and writes it back into `model_config.chatgpt_conversation_id`.

## PROVEN — exploration path so far

The historical implementation session preserves this progression:

1. inspect the ~892 MB export, 55 JSON chunks, 5,447 conversations;
2. write `/tmp/chatgpt-export/test_import.py` and dry-run flattening;
3. write 2 test conversations into the real existing DB and verify FTS/session search;
4. add GPT-conversation tests;
5. expand to a representative 57-session sample across all chunks;
6. attempt bulk/v2 imports;
7. investigate timeout/WAL behavior;
8. temporarily form, then disprove, the false `title UNIQUE` hypothesis;
9. add fail-first/schema/dedup/batch tests;
10. run the final missing-conversation pass;
11. repair the one orphan;
12. verify final counts and FTS.

Exact post-compaction scripts visible in the transcript include `test_fail_first.py`, `test_representative.py`, `test_dedup_batch.py`, `test_timeout.py`, `preflight.py`, and `import_final.py`. Earlier `test_import.py` survives only through the compacted historical handoff unless another transcript/tool artifact is recovered.

## NEW high-value lead — earlier June-15 import shape

Stage 2 found a tight cluster of **23 `chatgpt-export` sessions whose session IDs / `started_at` fall at 2026-06-15 05:00–05:01 UTC, while their earliest message timestamps are mostly from 2024**.

Examples include:

- `20260615_050044_67060f3b`
- `20260615_050047_66e2d6cf`
- `20260615_050049_670be7ed`
- `20260615_050104_67357525`

This is **not** evidence that all imported rows use import-time `started_at`; most rows align normally with historical timestamps. It is evidence that at least one earlier pass may have used a different session-time/session-ID policy and then survived final dedup.

A nearby Hermes-native session is especially high-value for the next extraction:

- `20260615_050051_06bd07d3` — `GPT記憶與偏好遷移`

The next stage should dump this full transcript and inspect the 23-row cluster together.

## Revised remaining questions

The old question “why do May/early-June rows look Hermes-shaped?” is withdrawn: Hermes was genuinely running from May 29.

Remaining work:

1. recover/locate the June-15 `GPT記憶與偏好遷移` transcript and explain the 23-row import-time cluster;
2. reconstruct the exact earlier import passes and script variants that produced the 4,654 rows already known to the final importer;
3. quantify the first ChatGPT insertion boundary using `messages.id` as an insertion-order proxy;
4. audit imported rows for native-only fingerprints (`parent_session_id`, `end_reason`, `cwd`, chat/thread/session keys, tool rows, Discord-style platform IDs) that could indicate a source-rewrite collision;
5. determine whether the final importer's `IntegrityError → source='chatgpt-export'` path ever touched a real Hermes-native session;
6. prove final imported-field fidelity/loss, especially rows created by earlier passes rather than the final script;
7. inspect only genuine non-ChatGPT runtime-shaped rows with evidence before **2026-05-29**, if any.

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
