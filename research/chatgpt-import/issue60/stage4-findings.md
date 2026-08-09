# Issue #60 — Stage 4 findings

Evidence archive: `issue60-stage4-evidence-20260809-224319.tar.gz`
Canonical DB SHA-256: `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104`

## PROVEN

- Safety receipt is clean: canonical counts `sessions=7268/messages=231513/gateway_routing=78`, `quick_check=ok`, FK violations `0`, immutable/query-only access, before/after SHA and file identity unchanged.
- All **5,447** final `chatgpt-export` sessions have `chatgpt_conversation_id`.
- All **5,447** satisfy the generated identity relation `session.id == UTC(started_at) + conversation_id[:8]`; mismatches = **0**.
- Stage 4's **4,655** `collision_relabel_residual_candidates` are not 4,655 native-collision fingerprints: every row has exactly one reason, `user_id_mismatch`. There are **0** rows in this set with generated-ID mismatch, missing conversation ID, native session fields, message tool fields, or tool calls.
- The count split is exact:
  - **792** rows have `user_id == conversation_id`;
  - **4,655** rows do not.
  Historical final-run evidence independently records **792** sessions inserted by the final pass, while **4,654** conversation IDs already existed before that pass and one v1 orphan was repaired afterward. This makes `user_id` behavior a very strong importer-generation discriminator.
- Legacy `model_config` fields survive widely from pre-final passes: `chatgpt_memory_scope=4254`, `chatgpt_is_study_mode=4254`, `chatgpt_is_do_not_remember=3143`, `chatgpt_voice=17`, `chatgpt_pinned_time=2`. The recovered final importer writes none of these keys.
- `message_count` drift exists in **1,174** imported sessions. Every drift is positive, and every affected session has `actual_messages / stored_message_count` exactly **3×, 4×, or 5×** (`32`, `618`, `524` sessions respectively). The total excess is **14,367** physical message rows.
- There are **4,918** repeated non-empty ChatGPT message UUID groups. Across those groups:
  - extra copies within the same session = `sum(copies - distinct_sessions)` = **14,365**;
  - cross-session shared copies = `sum(distinct_sessions - 1)` = **1,363**.
  The **14,365** same-session UUID replay rows plus the orphan's **2** empty-platform-ID rows equal the full **14,367** `message_count` excess exactly. This explains the stored-count drift as repeated message insertion/replay rather than random DB damage.
- The only non-UUID/empty imported `platform_message_id` rows are message IDs **178232** and **178233**, both in repaired v1 orphan `20231013_125540_0041efa3`, with placeholder timestamps `1.0` and `2.0`.

## STRONG INFERENCE

- The **792** `user_id == conversation_id` rows are the exact final-pass inserts, while the **4,655** mismatches are rows already created by earlier passes (the historical 4,654 existing conversation IDs plus the subsequently repaired orphan). The numerical and field-shape match is exact, but a pre-final DB snapshot / explicit inserted-ID list would be the strongest possible row-by-row proof.
- No surviving evidence supports the feared final-importer `IntegrityError -> UPDATE source='chatgpt-export'` path having relabelled a normal populated Hermes-native session. The Stage-3 native/tool/platform fingerprint audit was already empty; Stage 4 adds zero generated-ID or native/tool residuals. Treat the collision path as **not observed**, rather than claiming logical impossibility.
- The final DB's 89,434 imported message rows overstate the original per-session importer counts by 14,367 replay rows. Summed stored `session.message_count` is therefore **75,067** versus 89,434 physical rows.

## OPEN — narrow only

- **953** repeated ChatGPT UUID groups span more than one imported session. These may be legitimate shared/cloned ChatGPT ancestry (for example branched conversations) rather than importer replay. Need compare role/timestamp/content hashes across member sessions before classifying them.
- Inspect the repaired orphan's 8 physical message rows to determine how the two empty-ID placeholder rows relate to the later UUID-bearing rows.
- Final source-side loss accounting still requires either the original export or sufficiently strong historical transcript evidence for data that never entered Hermes (non-string parts, content references, attachments/tool structures, alternate branches).

Stage 5 (`extract_issue60_stage5.py`) is intentionally restricted to the first two OPEN DB-only questions and imported message-field presence. It does not restart broad provenance archaeology.
