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
PR_RE = re.compile(r"(?:\(#(\d+)\)|\bPR\s*#?(\d+)|\bpull request\s*#?(\d+))", re.IGNORECASE)
STATUSES = {
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


@dataclass(frozen=True)
class CommitRecord:
    sha: str
    subject: str
    parents: tuple[str, ...]
    is_merge: bool
    patch_id: str | None
    changed_files: tuple[str, ...]
    intent_id: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
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


def _patch_id(repo: Path, commit: str, is_merge: bool) -> str | None:
    show_args = ["show", "--format=", "--binary"]
    if is_merge:
        show_args[1:1] = ["-m", "--first-parent"]
    patch = subprocess.run(
        ["git", "-C", str(repo), *show_args, commit],
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


def _changed_files(repo: Path, commit: str, is_merge: bool) -> tuple[str, ...]:
    args = ["diff-tree", "--no-commit-id", "--name-only", "-r"]
    if is_merge:
        args.append("--first-parent")
    return tuple(sorted(filter(None, _git(repo, *args, commit).splitlines())))


def _intent_id(subject: str, commit: str) -> str:
    match = PR_RE.search(subject)
    if match:
        return f"pr:{next(group for group in match.groups() if group)}"
    return f"commit:{commit}"


def _commit_records(
    repo: Path, merge_base: str, fork_ref: str, intent_map: dict[str, str] | None = None
) -> list[CommitRecord]:
    raw = _git(repo, "log", "--reverse", "--format=%H%x09%P%x09%s", f"{merge_base}..{fork_ref}")
    records: list[CommitRecord] = []
    for row in raw.splitlines():
        sha, raw_parents, subject = row.split("\t", 2)
        parents = tuple(raw_parents.split())
        is_merge = len(parents) > 1
        records.append(
            CommitRecord(
                sha=sha,
                subject=subject,
                parents=parents,
                is_merge=is_merge,
                patch_id=_patch_id(repo, sha, is_merge),
                changed_files=_changed_files(repo, sha, is_merge),
                intent_id=(intent_map or {}).get(sha, _intent_id(subject, sha)),
            )
        )
    return records


def _upstream_patch_ids(repo: Path, merge_base: str, upstream_ref: str, touched_files: set[str]) -> set[str]:
    """Compute patch IDs only for upstream commits that can match fork files."""
    raw = _git(repo, "log", "--no-merges", "--format=%H", "--name-only", f"{merge_base}..{upstream_ref}")
    patch_ids: set[str] = set()
    current_commit: str | None = None
    current_files: set[str] = set()

    def flush() -> None:
        if current_commit is None or not current_files.intersection(touched_files):
            return
        patch_id = _patch_id(repo, current_commit, False)
        if patch_id:
            patch_ids.add(patch_id)

    for line in raw.splitlines():
        if SHA_RE.fullmatch(line):
            flush()
            current_commit = line
            current_files = set()
        elif line:
            current_files.add(line)
    flush()
    return patch_ids


def _validate_evidence(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("intents", {}), dict):
        raise AuditInputError("evidence must be an object with an 'intents' object")
    validated: dict[str, dict[str, Any]] = {}
    for intent_id, item in raw["intents"].items():
        if not isinstance(intent_id, str) or not isinstance(item, dict):
            raise AuditInputError("each evidence intent must map a string id to an object")
        status = item.get("upstream_status")
        disposition = item.get("disposition")
        if status is not None and status not in STATUSES:
            raise AuditInputError(f"invalid upstream_status for {intent_id}: {status}")
        if disposition is not None and disposition not in DISPOSITIONS:
            raise AuditInputError(f"invalid disposition for {intent_id}: {disposition}")
        evidence = item.get("evidence", [])
        contracts = item.get("behavioral_contracts", [])
        if not isinstance(evidence, list) or not isinstance(contracts, list):
            raise AuditInputError(f"evidence and behavioral_contracts must be lists: {intent_id}")
        if not all(isinstance(value, str) for value in evidence + contracts):
            raise AuditInputError(f"evidence and behavioral_contracts must contain strings: {intent_id}")
        validated[intent_id] = {
            **item,
            "evidence": evidence,
            "behavioral_contracts": contracts,
        }
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
    if not isinstance(commits, dict) or not all(
        isinstance(sha, str) and isinstance(intent_id, str) and intent_id
        for sha, intent_id in commits.items()
    ):
        raise AuditInputError("intent map must be an object with a string-to-string 'commits' object")
    return commits


def _derived_classification(records: list[CommitRecord], upstream_patch_ids: set[str]) -> tuple[str, str, str]:
    patches = [record.patch_id for record in records if not record.is_merge and record.patch_id]
    exact_count = sum(patch_id in upstream_patch_ids for patch_id in patches)
    if patches and exact_count == len(patches):
        return "EXACT_UPSTREAM", "DROP", "high"
    if exact_count:
        return "PARTIAL_UPSTREAM", "SPLIT", "medium"
    return "NEEDS_REVIEW", "NEEDS_REVIEW", "low"


def build_inventory(
    repo: Path,
    fork_ref: str,
    upstream_ref: str,
    merge_base: str,
    evidence: dict[str, dict[str, Any]] | None = None,
    intent_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    for name, value in (("fork_ref", fork_ref), ("upstream_ref", upstream_ref), ("merge_base", merge_base)):
        _require_sha(name, value)
        _git(repo, "cat-file", "-e", f"{value}^{{commit}}")
    actual_base = _git(repo, "merge-base", fork_ref, upstream_ref)
    if actual_base != merge_base:
        raise AuditInputError(f"merge_base mismatch: expected {merge_base}, got {actual_base}")

    records = _commit_records(repo, merge_base, fork_ref, intent_map)
    record_shas = {record.sha for record in records}
    unknown_commit_map = sorted(set(intent_map or {}) - record_shas)
    if unknown_commit_map:
        raise AuditInputError(
            f"intent map references commits outside the fork range: {', '.join(unknown_commit_map)}"
        )
    touched_files = {file for record in records for file in record.changed_files}
    upstream_patch_ids = _upstream_patch_ids(repo, merge_base, upstream_ref, touched_files)
    by_intent: dict[str, list[CommitRecord]] = {}
    for record in records:
        by_intent.setdefault(record.intent_id, []).append(record)

    evidence = evidence or {}
    unknown_evidence = sorted(set(evidence) - set(by_intent))
    if unknown_evidence:
        raise AuditInputError(f"evidence references unknown intents: {', '.join(unknown_evidence)}")

    intents: list[dict[str, Any]] = []
    for intent_id, grouped in by_intent.items():
        derived_status, derived_disposition, derived_confidence = _derived_classification(
            grouped, upstream_patch_ids
        )
        override = evidence.get(intent_id, {})
        status = override.get("upstream_status", derived_status)
        disposition = override.get("disposition", derived_disposition)
        confidence = override.get("confidence", derived_confidence)
        if confidence not in {"high", "medium", "low"}:
            raise AuditInputError(f"invalid confidence for {intent_id}: {confidence}")
        exact_commits = [record.sha for record in grouped if record.patch_id in upstream_patch_ids]
        intents.append(
            {
                "id": intent_id,
                "subject": grouped[0].subject,
                "commits": [
                    {
                        "sha": record.sha,
                        "subject": record.subject,
                        "parents": list(record.parents),
                        "is_merge": record.is_merge,
                        "patch_id": record.patch_id,
                        "changed_files": list(record.changed_files),
                    }
                    for record in grouped
                ],
                "affected_files": sorted({file for record in grouped for file in record.changed_files}),
                "merge_commits": [record.sha for record in grouped if record.is_merge],
                "historical_prs": sorted(
                    {intent_id.removeprefix("pr:")} if intent_id.startswith("pr:") else set()
                ),
                "exact_upstream_commits": exact_commits,
                "upstream_status": status,
                "disposition": disposition,
                "confidence": confidence,
                "evidence": list(override.get("evidence", []))
                or (["stable patch-id matches upstream"] if derived_status == "EXACT_UPSTREAM" else []),
                "behavioral_contracts": list(override.get("behavioral_contracts", [])),
            }
        )
    intents.sort(key=lambda item: item["commits"][0]["sha"])
    return {
        "schema_version": 2,
        "inputs": {"fork_ref": fork_ref, "upstream_ref": upstream_ref, "merge_base": merge_base},
        "intents": intents,
        "accounting": {
            "historical_commits": len(records),
            "mapped_commits": sum(len(intent["commits"]) for intent in intents),
            "unaccounted_commits": [],
            "complete": sum(len(intent["commits"]) for intent in intents) == len(records),
        },
    }


def render_markdown(inventory: dict[str, Any]) -> str:
    inputs = inventory["inputs"]
    lines = [
        "# Fork archaeology inventory",
        "",
        f"- Fork ref: `{inputs['fork_ref']}`",
        f"- Upstream ref: `{inputs['upstream_ref']}`",
        f"- Merge base: `{inputs['merge_base']}`",
        "",
        "| Intent | Historical commits | Upstream status | Disposition | Confidence |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for intent in inventory["intents"]:
        lines.append(
            f"| `{intent['id']}` | {len(intent['commits'])} | "
            f"`{intent['upstream_status']}` | `{intent['disposition']}` | `{intent['confidence']}` |"
        )
    lines += ["", "## Evidence", ""]
    for intent in inventory["intents"]:
        lines.append(f"### `{intent['id']}` — {intent['subject']}")
        lines.append("")
        commit_names = ", ".join(f"`{commit['sha'][:12]}`" for commit in intent["commits"])
        lines.append(f"- Commits: {commit_names}")
        lines.append(f"- Files: {', '.join(f'`{file}`' for file in intent['affected_files']) or '(none)'}")
        lines.append(f"- Evidence: {'; '.join(intent['evidence']) or 'none; review required'}")
        if intent["behavioral_contracts"]:
            lines.append(f"- Behavioral contracts: {'; '.join(intent['behavioral_contracts'])}")
        lines.append("")
    complete = inventory["accounting"]["complete"]
    lines.append(f"Completeness: **{'PASS' if complete else 'FAIL'}** — every discovered commit has an intent mapping.")
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
        inventory = build_inventory(
            args.repo,
            args.fork_ref,
            args.upstream_ref,
            args.merge_base,
            _load_evidence(args.evidence_file),
            _load_intent_map(args.intent_map),
        )
    except AuditInputError as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "inventory.md").write_text(render_markdown(inventory), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
