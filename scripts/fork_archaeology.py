#!/usr/bin/env python3
"""Build a reproducible, evidence-backed inventory of fork history."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PAREN_REF_RE = re.compile(r"\(#(\d+)\)")
PR_REF_RE = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)", re.IGNORECASE)
STATUSES = {
    "PATCH_EQUIVALENT_UPSTREAM_HISTORY",
    "EXACT_UPSTREAM",
    "SEMANTIC_UPSTREAM",
    "PARTIAL_UPSTREAM",
    "FORK_ONLY",
    "LOST_IN_FORK",
    "NEEDS_REVIEW",
}
DISPOSITIONS = {"DROP", "KEEP", "PORT", "SPLIT", "NEEDS_REVIEW"}


class AuditInputError(ValueError):
    """Raised when an archaeology run is not reproducible or evidence is invalid."""


def _safe_repo(repo: Path) -> str:
    return repo.resolve().as_posix()


@dataclass(frozen=True)
class CommitRecord:
    sha: str
    subject: str
    parents: tuple[str, ...]
    is_merge: bool
    patch_id: str | None
    changed_files: tuple[str, ...]
    provisional_bucket: str
    issue_refs: tuple[str, ...]
    pr_refs: tuple[str, ...]
    upstream_reachable: bool


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={_safe_repo(repo)}", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise AuditInputError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _require_sha(name: str, value: str) -> None:
    if not SHA_RE.fullmatch(value):
        raise AuditInputError(f"{name} must be a full 40-character commit SHA")


def _patch_id(repo: Path, commit: str) -> str | None:
    patch = subprocess.run(
        ["git", "-c", f"safe.directory={_safe_repo(repo)}", "-C", str(repo), "show", "--format=", "--binary", commit],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if patch.returncode:
        raise AuditInputError(patch.stderr.strip())
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=patch.stdout,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise AuditInputError(result.stderr.strip())
    return result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else None


def _changed_files(repo: Path, commit: str) -> tuple[str, ...]:
    return tuple(sorted(filter(None, _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())))


def _refs(subject: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parenthesized = [match.group(1) for match in PAREN_REF_RE.finditer(subject)]
    explicit_prs = [match.group(1) for match in PR_REF_RE.finditer(subject)]
    if explicit_prs:
        return tuple(parenthesized), tuple(dict.fromkeys(explicit_prs))
    if parenthesized:
        # Squash messages commonly end in the GitHub PR marker: title (#issue) (#pr).
        return tuple(parenthesized[:-1]), (parenthesized[-1],)
    return (), ()


def _bucket(subject: str, commit: str, intent_map: dict[str, str]) -> str:
    if commit in intent_map:
        return intent_map[commit]
    _, prs = _refs(subject)
    return f"pr:{prs[-1]}" if prs else f"commit:{commit}"


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={_safe_repo(repo)}", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise AuditInputError(result.stderr.decode(errors="replace").strip())
    return result.returncode == 0


def _commit_records(repo: Path, merge_base: str, fork_ref: str, upstream_ref: str, intent_map: dict[str, str]) -> list[CommitRecord]:
    raw = _git(repo, "log", "--reverse", "--format=%H%x09%P%x09%s", f"{merge_base}..{fork_ref}")
    records: list[CommitRecord] = []
    for row in raw.splitlines():
        sha, raw_parents, subject = row.split("\t", 2)
        parents = tuple(raw_parents.split())
        issue_refs, pr_refs = _refs(subject)
        is_merge = len(parents) > 1
        records.append(
            CommitRecord(
                sha=sha,
                subject=subject,
                parents=parents,
                is_merge=is_merge,
                patch_id=None if is_merge else _patch_id(repo, sha),
                changed_files=_changed_files(repo, sha),
                provisional_bucket=_bucket(subject, sha, intent_map),
                issue_refs=issue_refs,
                pr_refs=pr_refs,
                upstream_reachable=_is_ancestor(repo, sha, upstream_ref),
            )
        )
    return records


def _upstream_patch_ids(repo: Path, merge_base: str, upstream_ref: str, touched_files: set[str]) -> dict[str, list[str]]:
    """Return patch-id provenance for relevant upstream history in one Git batch."""
    raw = _git(repo, "log", "--no-merges", "--format=%H", "--name-only", f"{merge_base}..{upstream_ref}")
    candidates: list[tuple[str, set[str]]] = []
    current_commit: str | None = None
    current_files: set[str] = set()
    for line in raw.splitlines():
        if SHA_RE.fullmatch(line):
            if current_commit is not None:
                candidates.append((current_commit, current_files))
            current_commit, current_files = line, set()
        elif line:
            current_files.add(line)
    if current_commit is not None:
        candidates.append((current_commit, current_files))

    # One Git process plus one patch-id process handles the complete candidate set.
    commits = [sha for sha, files in candidates if files.intersection(touched_files)]
    if not commits:
        return {}
    patches = subprocess.run(
        ["git", "-c", f"safe.directory={_safe_repo(repo)}", "-C", str(repo), "show", "--format=%H", "--binary", "--no-ext-diff", *commits],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if patches.returncode:
        raise AuditInputError(patches.stderr.strip())
    patch_ids = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=patches.stdout,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if patch_ids.returncode:
        raise AuditInputError(patch_ids.stderr.strip())
    ids: dict[str, list[str]] = {}
    found = [line.split(maxsplit=1)[0] for line in patch_ids.stdout.splitlines() if line.strip()]
    if len(found) != len(commits):
        raise AuditInputError("batched upstream patch-id output did not preserve commit mapping")
    for sha, patch_id in zip(commits, found):
        ids.setdefault(patch_id, []).append(sha)
    return ids


def _validate_evidence(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AuditInputError("evidence must be an object with an 'intents' object")
    items = raw["intents"] if "intents" in raw else raw
    if not isinstance(items, dict):
        raise AuditInputError("evidence must be an object with an 'intents' object")
    validated: dict[str, dict[str, Any]] = {}
    for bucket, item in items.items():
        if not isinstance(bucket, str) or not isinstance(item, dict):
            raise AuditInputError("each evidence bucket must map a string id to an object")
        status = item.get("upstream_status")
        disposition = item.get("disposition")
        if status is not None and status not in STATUSES:
            raise AuditInputError(f"invalid upstream_status for {bucket}: {status}")
        if disposition is not None and disposition not in DISPOSITIONS:
            raise AuditInputError(f"invalid disposition for {bucket}: {disposition}")
        evidence = item.get("evidence", [])
        contracts = item.get("behavioral_contracts", [])
        if not isinstance(evidence, list) or not isinstance(contracts, list):
            raise AuditInputError(f"evidence and behavioral_contracts must be lists: {bucket}")
        if not all(isinstance(value, str) for value in evidence + contracts):
            raise AuditInputError(f"evidence and behavioral_contracts must contain strings: {bucket}")
        if disposition == "DROP" and (status not in {"EXACT_UPSTREAM", "SEMANTIC_UPSTREAM"} or not evidence):
            raise AuditInputError(f"DROP requires upstream ownership evidence: {bucket}")
        if status in {"EXACT_UPSTREAM", "SEMANTIC_UPSTREAM"} and (not evidence or not contracts):
            raise AuditInputError(f"{status} requires evidence and behavioral contracts: {bucket}")
        if status == "LOST_IN_FORK" and not evidence:
            raise AuditInputError(f"LOST_IN_FORK requires historical/current evidence: {bucket}")
        validated[bucket] = {**item, "evidence": evidence, "behavioral_contracts": contracts}
    return validated


def _load_evidence(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        return _validate_evidence(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditInputError(f"cannot read evidence file {path}: {error}") from error


def _load_intent_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditInputError(f"cannot read intent map {path}: {error}") from error
    commits = raw.get("commits") if isinstance(raw, dict) else None
    if not isinstance(commits, dict) or not all(isinstance(sha, str) and isinstance(bucket, str) and bucket for sha, bucket in commits.items()):
        raise AuditInputError("intent map must be an object with a string-to-string 'commits' object")
    return commits


def _derived_classification(records: list[CommitRecord], upstream_matches: dict[str, list[str]]) -> tuple[str, str, str]:
    patches = [record.patch_id for record in records if record.patch_id]
    exact_count = sum(patch_id in upstream_matches for patch_id in patches)
    if patches and exact_count:
        return "PATCH_EQUIVALENT_UPSTREAM_HISTORY", "NEEDS_REVIEW", "high" if exact_count == len(patches) else "medium"
    return "NEEDS_REVIEW", "NEEDS_REVIEW", "low"


def build_inventory(repo: Path, fork_ref: str, upstream_ref: str, merge_base: str, evidence: dict[str, dict[str, Any]] | None = None, intent_map: dict[str, str] | None = None) -> dict[str, Any]:
    for name, value in (("fork_ref", fork_ref), ("upstream_ref", upstream_ref), ("merge_base", merge_base)):
        _require_sha(name, value)
        _git(repo, "cat-file", "-e", f"{value}^{{commit}}")
    actual_base = _git(repo, "merge-base", fork_ref, upstream_ref)
    if actual_base != merge_base:
        raise AuditInputError(f"merge_base mismatch: expected {merge_base}, got {actual_base}")

    explicit_map = intent_map or {}
    records = _commit_records(repo, merge_base, fork_ref, upstream_ref, explicit_map)
    record_shas = {record.sha for record in records}
    unknown_map = sorted(set(explicit_map) - record_shas)
    if unknown_map:
        raise AuditInputError(f"intent map references commits outside the fork range: {', '.join(unknown_map)}")
    touched_files = {file for record in records for file in record.changed_files}
    upstream_matches = _upstream_patch_ids(repo, merge_base, upstream_ref, touched_files)
    evidence = _validate_evidence(evidence or {})
    buckets: dict[str, list[CommitRecord]] = {}
    for record in records:
        buckets.setdefault(record.provisional_bucket, []).append(record)
    unknown_evidence = sorted(set(evidence) - set(buckets))
    if unknown_evidence:
        raise AuditInputError(f"evidence references unknown buckets: {', '.join(unknown_evidence)}")

    groups: list[dict[str, Any]] = []
    for bucket, grouped in buckets.items():
        changes = [record for record in grouped if not record.is_merge]
        merge_events = [record for record in grouped if record.is_merge]
        derived_status, derived_disposition, derived_confidence = _derived_classification(changes, upstream_matches)
        override = evidence.get(bucket, {})
        status = override.get("upstream_status", derived_status)
        disposition = override.get("disposition", derived_disposition)
        confidence = override.get("confidence", derived_confidence)
        if confidence not in {"high", "medium", "low"}:
            raise AuditInputError(f"invalid confidence for {bucket}: {confidence}")
        matching = [
            {"fork_commit": record.sha, "patch_id": record.patch_id, "upstream_commits": upstream_matches.get(record.patch_id, [])}
            for record in changes
            if record.patch_id in upstream_matches
        ]
        groups.append({
            "id": bucket,
            "record_type": "provenance_bucket",
            "subject": grouped[0].subject,
            "commits": [{"sha": r.sha, "subject": r.subject, "parents": list(r.parents), "is_merge": r.is_merge, "patch_id": r.patch_id, "changed_files": list(r.changed_files), "graph_presence": {"upstream_reachable": r.upstream_reachable, "fork_exclusive": not r.upstream_reachable}, "issue_refs": list(r.issue_refs), "pr_refs": list(r.pr_refs)} for r in changes],
            "merge_events": [{"sha": r.sha, "subject": r.subject, "parents": list(r.parents), "changed_files": list(r.changed_files), "pr_refs": list(r.pr_refs)} for r in merge_events],
            "affected_files": sorted({file for record in grouped for file in record.changed_files}),
            "work_item_provenance": {"issue_refs": sorted({ref for r in grouped for ref in r.issue_refs}), "pr_refs": sorted({ref for r in grouped for ref in r.pr_refs})},
            "patch_matches": matching,
            "exact_upstream_commits": sorted({sha for match in matching for sha in match["upstream_commits"]}),
            "upstream_status": status,
            "disposition": disposition,
            "confidence": confidence,
            "evidence": list(override.get("evidence", [])),
            "behavioral_contracts": list(override.get("behavioral_contracts", [])),
        })
    groups.sort(key=lambda item: item["commits"][0]["sha"] if item["commits"] else item["merge_events"][0]["sha"])
    mapped = sum(1 for record in records if record.sha in explicit_map)
    unaccounted = [] if mapped == len(records) else sorted(record.sha for record in records if record.sha not in explicit_map)
    return {
        "schema_version": 3,
        "inputs": {"fork_ref": fork_ref, "upstream_ref": upstream_ref, "merge_base": merge_base},
        "provenance_buckets": groups,
        "accounting": {
            "discovery_coverage": {"historical_commits": len(records), "emitted_commit_records": len(records), "complete": True},
            "capability_accounting": {"explicitly_accounted_commits": mapped, "unaccounted_commits": unaccounted, "complete": bool(explicit_map) and not unaccounted},
        },
    }


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "\\`").replace("\n", " ")


def render_markdown(inventory: dict[str, Any]) -> str:
    inputs = inventory["inputs"]
    lines = ["# Fork archaeology inventory", "", f"- Fork ref: `{inputs['fork_ref']}`", f"- Upstream ref: `{inputs['upstream_ref']}`", f"- Merge base: `{inputs['merge_base']}`", "", "| Provenance bucket | Change records | Merge events | Status | Disposition | Confidence |", "| --- | ---: | ---: | --- | --- | --- |"]
    for group in inventory["provenance_buckets"]:
        lines.append(f"| `{_md(group['id'])}` | {len(group['commits'])} | {len(group['merge_events'])} | `{group['upstream_status']}` | `{group['disposition']}` | `{group['confidence']}` |")
    lines += ["", "## Evidence", ""]
    for group in inventory["provenance_buckets"]:
        changes = ", ".join(f"`{item['sha'][:12]}`" for item in group["commits"]) or "(none)"
        merges = ", ".join(f"`{item['sha'][:12]}`" for item in group["merge_events"]) or "(none)"
        files = ", ".join(f"`{_md(item)}`" for item in group["affected_files"]) or "(none)"
        matches = ", ".join(f"`{item[:12]}`" for item in group["exact_upstream_commits"]) or "none"
        evidence = "; ".join(_md(item) for item in group["evidence"]) or "none; review required"
        lines += [f"### `{_md(group['id'])}` - {_md(group['subject'])}", "", f"- Changes: {changes}", f"- Merge events: {merges}", f"- Files: {files}", f"- Upstream matches: {matches}", f"- Evidence: {evidence}"]
        if group["behavioral_contracts"]:
            lines.append(f"- Behavioral contracts: {'; '.join(_md(item) for item in group['behavioral_contracts'])}")
        lines.append("")
    discovery = inventory["accounting"]["discovery_coverage"]
    capability = inventory["accounting"]["capability_accounting"]
    lines.append(f"Discovery completeness: **{'PASS' if discovery['complete'] else 'FAIL'}** — {discovery['emitted_commit_records']} of {discovery['historical_commits']} records emitted.")
    lines.append(f"Capability accounting: **{'PASS' if capability['complete'] else 'FAIL'}** - explicit disposition mapping is required before this gate can pass.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fork-ref", required=True)
    parser.add_argument("--upstream-ref", required=True)
    parser.add_argument("--merge-base", required=True)
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--intent-map", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        inventory = build_inventory(args.repo, args.fork_ref, args.upstream_ref, args.merge_base, _load_evidence(args.evidence_file), _load_intent_map(args.intent_map))
    except AuditInputError as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "inventory.md").write_text(render_markdown(inventory), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
