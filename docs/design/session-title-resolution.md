# Session title resolution — exact binding and numbered lineage

Status: **research contract for Phase-2 reconstruction #130**. No production implementation is carried by this document.

Research authority: [`docs/research/issue-130-current-upstream-session-title-resolution.md`](../research/issue-130-current-upstream-session-title-resolution.md).  
Historical accepted fork implementation: [#15](https://github.com/Skywind5487/hermes-agent/issues/15) / [PR #88](https://github.com/Skywind5487/hermes-agent/pull/88).

## Contract

This surface is **exact binding**, not fuzzy discovery.

For a literal base title `base`, a numbered continuation is valid iff its complete title is:

```text
base + " #" + one-or-more ASCII digits [0-9]
```

Consequences:

- `%`, `_`, `\\`, `#`, punctuation, Unicode, and CJK characters inside `base` are literal title characters.
- SQL `LIKE` may be used only as bounded candidate selection and must escape `%`, `_`, and `\\` with the shared upstream `hermes_state_common.escape_like` helper.
- Every SQL candidate must still pass the literal `base + " #" + ASCII-digits` predicate before it can bind as a continuation.
- Unicode digit classes are not the grammar: `foo #２` is not a numbered continuation.
- Near-misses such as `foo #bar`, `foo #`, `foo # 2`, `foo #2x`, `foo #2.0`, and `foo #2 ` are not continuations.
- A deeper title such as `foo #2 #5` is not a direct numbered child of `foo` and must not inflate `foo`'s next lineage number.
- `#1` and leading-zero forms such as `#01` remain syntactically valid because they are ASCII decimal strings; the unnumbered root still occupies number 1 for generation.

## Current-upstream reconstruction seam

At the 2026-08-19 research pin, current upstream already owns:

- exact session-title lookup;
- the shared SQL-LIKE escape helper;
- direct LIKE candidate selection in `resolve_session_by_title()` and `get_next_title_in_lineage()`.

Current upstream does **not** route this binding through the fork's historical Session Metadata FTS helpers. Therefore #130 must preserve the contract rather than replay PR #88's old `_fts_numbered_variants` implementation shape.

The smallest current reconstruction is one shared strict numbered-variant predicate reused by:

1. `resolve_session_by_title()` to post-filter escaped LIKE candidates before choosing the latest continuation;
2. `get_next_title_in_lineage()` to strip/count only direct ASCII `#N` variants.

No FTS schema/lifecycle work, fuzzy title search, new metadata index, or second escaping helper belongs in this line.

## Boundary with Session Search

- #128 owns fuzzy metadata discovery, ranking, normalization, `display_name`, and search-index/routing concerns.
- #129 owns compression-aware search lineage/dedupe/hydration composition.
- #130 owns literal exact/numbered title-family binding.

File/caller overlap is shared substrate, not evidence of a hard runtime dependency.
