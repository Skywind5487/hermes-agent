# #19 implementation: retire legacy simple-tokenizer FTS schema debt

Status: **implemented**  
Research: **#40** (`issue-40-simple-tokenizer-eol.md`)  
Base: `196b90441` (origin/dev, post-#12/#30 convergence)  
Branch: `feat/19-simple-tokenizer-eol`

## What changed

- **Removed** the fork-local loadable-`simple` compatibility shim:
  `simple_tokenizer_so_path()`, `load_simple_extension()`, the
  `_simple_loaded` writer flag, read-connection propagation, and the
  `load_simple_extension()` call in `_db_opens_cleanly()`. No supported
  post-#12 DB state needs `simple` at open or write time (per #40).
- **Added** `_sanitize_retired_simple_residue()`: a narrow pre-init
  writable pass that structurally detaches ONLY the exact historical Hermes
  `tokenize='simple'` residue (`messages_fts_trigram` /
  `sessions_fts_trigram` roots, their five FTS5 shadow tables, and the
  lane's sync triggers) before `_init_schema()` can touch the unloadable
  tokenizer. The retired vtable is never connected (`writable_schema`
  surgery + `DROP TRIGGER`); canonical `sessions` / `messages` rows are
  preserved and the modern FTS lifecycle converges. Modern/foreign
  same-name objects fail closed, untouched.
- **Deleted** the dead `tests/test_optional_cjk_tokenizer_fallback.py`
  (referenced removed shim symbols; asserted the retired
  simple-as-optional-tokenizer policy).
- **Added** `tests/test_simple_tokenizer_eol.py`: message/session residue
  convergence, unknown same-name fail-closed, loader-gone, and
  health-probe-without-simple policy pins.

No `SCHEMA_VERSION` / `FTS_STORAGE_VERSION` bump. No new durable marker or
state machine. Historical research notes (#40) remain historical.

## Validation

`tests/test_simple_tokenizer_eol.py` plus the existing FTS lifecycle,
storage-v2, session-recovery, session-search, and core `test_hermes_state`
suites all pass (500+ tests across the targeted runs).
