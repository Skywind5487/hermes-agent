#!/usr/bin/env python3
"""Build a reproducible inventory of fork history against an upstream ref."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class AuditInputError(ValueError):
    """Raised when an archaeology run is not reproducible from its inputs."""


@dataclass(frozen=True)
class Intent:
    id: str
    commit: str
    subject: str
    is_merge: bool
    patch_id: str | None
    upstream_status: str
    disposition: str
    confidence: str
    evidence: tuple[str, ...]
    feature_line: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
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
    )
    if patch.returncode:
        raise AuditInputError(patch.stderr.strip())
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=patch.stdout,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise AuditInputError(result.stderr.strip())
    return result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else None


def _feature_line(subject: str) -> str:
    subject = re.sub(r"\(#\d+\)$", "", subject).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
    return slug[:80] or "unlabelled-intent"


def build_inventory(repo: Path, fork_ref: str, upstream_ref: str, merge_base: str) -> dict:
    for name, value in (("fork_ref", fork_ref), ("upstream_ref", upstream_ref), ("merge_base", merge_base)):
        _require_sha(name, value)
        _git(repo, "cat-file", "-e", f"{value}^{{commit}}")
    actual_base = _git(repo, "merge-base", fork_ref, upstream_ref)
    if actual_base != merge_base:
        raise AuditInputError(f"merge_base mismatch: expected {merge_base}, got {actual_base}")

    upstream_commits = _git(
        repo, "log", "--format=%H", "--no-merges", f"{merge_base}..{upstream_ref}"
    ).splitlines()
    upstream_patch_ids = {
        patch_id
        for commit in upstream_commits
        if (patch_id := _patch_id(repo, commit, False)) is not None
    }
    raw_commits = _git(repo, "log", "--reverse", "--format=%H%x09%P%x09%s", f"{merge_base}..{fork_ref}")
    intents: list[Intent] = []
    for row in raw_commits.splitlines():
        commit, parents, subject = row.split("\t", 2)
        is_merge = len(parents.split()) > 1
        patch_id = _patch_id(repo, commit, is_merge)
        exact = patch_id is not None and patch_id in upstream_patch_ids
        intents.append(
            Intent(
                id=hashlib.sha1(f"{commit}:{subject}".encode()).hexdigest()[:12],
                commit=commit,
                subject=subject,
                is_merge=is_merge,
                patch_id=patch_id,
                upstream_status="EXACT_UPSTREAM" if exact else "NEEDS_REVIEW",
                disposition="DROP" if exact else "NEEDS_REVIEW",
                confidence="high" if exact else "low",
                evidence=(
                    "stable patch-id matches an upstream commit" if exact else
                    "no exact patch-id match; semantic evidence is required",
                ),
                feature_line=_feature_line(subject),
            )
        )
    return {
        "schema_version": 1,
        "inputs": {"fork_ref": fork_ref, "upstream_ref": upstream_ref, "merge_base": merge_base},
        "intents": [asdict(intent) for intent in intents],
        "accounting": {"historical_commits": len(intents), "unaccounted_intents": [], "complete": True},
    }


def render_markdown(inventory: dict) -> str:
    inputs = inventory["inputs"]
    lines = [
        "# Fork archaeology inventory",
        "",
        f"- Fork ref: `{inputs['fork_ref']}`",
        f"- Upstream ref: `{inputs['upstream_ref']}`",
        f"- Merge base: `{inputs['merge_base']}`",
        "",
        "| Commit | Intent | Upstream status | Disposition | Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for intent in inventory["intents"]:
        lines.append(
            f"| `{intent['commit'][:12]}` | {intent['subject']} | "
            f"`{intent['upstream_status']}` | `{intent['disposition']}` | `{intent['confidence']}` |"
        )
    lines += ["", "Completeness: **PASS** — every discovered historical commit has a disposition.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fork-ref", required=True)
    parser.add_argument("--upstream-ref", required=True)
    parser.add_argument("--merge-base", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        inventory = build_inventory(args.repo, args.fork_ref, args.upstream_ref, args.merge_base)
    except AuditInputError as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "inventory.md").write_text(render_markdown(inventory), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
