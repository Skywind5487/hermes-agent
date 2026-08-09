# Issue #60 evidence locators

This file records **where** the migration evidence lives without publishing the full raw historical transcript.

The repository is public, so the full stage-2 transcript bundle is intentionally kept private/local. Its SHA-256 is recorded in `evidence-manifest.json`.

## Origin / intake session

- Session: `20260616_054313_151a9806`
- Title: `ChatGPT数据整合计划`
- Source: `discord`
- Migration thread starts at message **90307**:
  - user says the ChatGPT data export is ready;
  - asks to inspect email/data/DB format;
  - asks for an integration plan / preview.
- The session **changes topic at message 90416**:
  - user says the ZIP is huge;
  - asks to investigate the Windows C: drive;
  - the following C-drive / meta-thinking / personality work is not importer evidence.

Therefore this long session is an **origin/intake locator only**, not the full importer implementation transcript.

## Main implementation session

- Session: `20260616_100300_58622e7c`
- Title: `ChatGPT搬遷討論回顧`
- Source: `discord`

Important evidence inside it:

- message **179210**: context-compaction summary preserving the earlier migration exploration;
- message **179228–179234**: dedup/field/content-type exploration;
- message **179242–179267**: failed UNIQUE-title diagnosis, schema verification, and fail-first retrospective;
- message **185079–185091**: dedup correction, real import timing, and creation of `chatgpt-to-hermes-import` skill;
- message **188423–188425**: pre-flight DB audit and the `write_file` call containing the final `/tmp/chatgpt-export/import_final.py`;
- message **188428**: exact final importer execution output;
- messages **188429–188461**: orphan cleanup / final checks / FTS verification.

## Artifact strength

`recovered-import_final.py` is reconstructed from the exact historical `write_file` tool-call payload, not from a prose summary.

`import-final-run-output.txt` is reconstructed from the exact historical terminal tool result.

The pre-compaction exploration survives partly through a context-compaction summary. Treat claims that exist **only** in that summary as supporting historical context, not as equal in strength to the exact script/tool results.
