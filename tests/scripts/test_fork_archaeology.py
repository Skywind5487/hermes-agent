import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "fork_archaeology.py"


def load_module():
    spec = spec_from_file_location("fork_archaeology_test_module", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str, filename: str, content: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True)
    return git(repo, "rev-parse", "HEAD")


def repo_with_branches(tmp_path: Path, fork_message: str = "fork feature"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    fork_ref = commit(repo, fork_message, "feature.txt", "fork\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    upstream_ref = commit(repo, "same feature upstream", "feature.txt", "fork\n")
    return repo, base, fork_ref, upstream_ref


def test_patch_history_is_candidate_only_and_reports_upstream_sha(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path)
    inventory = load_module().build_inventory(repo, fork_ref, upstream_ref, base)
    group = inventory["provenance_buckets"][0]

    assert group["upstream_status"] == "PATCH_EQUIVALENT_UPSTREAM_HISTORY"
    assert group["disposition"] == "NEEDS_REVIEW"
    assert group["exact_upstream_commits"] == [upstream_ref]
    assert group["patch_matches"][0]["fork_commit"] == fork_ref
    assert inventory["accounting"]["discovery_coverage"]["complete"] is True
    assert inventory["accounting"]["capability_accounting"]["explicitly_accounted_commits"] == 0
    assert inventory["accounting"]["capability_accounting"]["complete"] is False


def test_upstream_revert_does_not_authorize_drop(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    fork_ref = commit(repo, "add behavior", "feature.txt", "feature\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    commit(repo, "add behavior upstream", "feature.txt", "feature\n")
    subprocess.run(["git", "-C", str(repo), "revert", "--no-edit", "HEAD"], check=True, capture_output=True)
    upstream_ref = git(repo, "rev-parse", "HEAD")
    group = load_module().build_inventory(repo, fork_ref, upstream_ref, base)["provenance_buckets"][0]

    assert group["upstream_status"] == "PATCH_EQUIVALENT_UPSTREAM_HISTORY"
    assert group["disposition"] == "NEEDS_REVIEW"


def test_merge_commit_is_an_event_not_a_change_record(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path, "unrelated fork")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "topic", base], check=True)
    feature = commit(repo, "feature work", "topic.txt", "topic\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-B", "fork", fork_ref], check=True)
    subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", "topic"], check=True, capture_output=True)
    merged_ref = git(repo, "rev-parse", "HEAD")
    inventory = load_module().build_inventory(repo, merged_ref, upstream_ref, base)

    assert sum(len(group["merge_events"]) for group in inventory["provenance_buckets"]) == 1
    assert all(not record["is_merge"] for group in inventory["provenance_buckets"] for record in group["commits"])
    assert feature in [record["sha"] for group in inventory["provenance_buckets"] for record in group["commits"]]


def test_refs_must_be_full_shas(tmp_path: Path):
    module = load_module()
    with pytest.raises(module.AuditInputError, match="full 40-character"):
        module.build_inventory(tmp_path, "main", "0" * 40, "0" * 40)


def test_issue_and_pr_refs_use_final_squash_marker(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path, "Session metadata FTS (#25) (#59)")
    group = load_module().build_inventory(repo, fork_ref, upstream_ref, base)["provenance_buckets"][0]
    record = group["commits"][0]

    assert record["issue_refs"] == ["25"]
    assert record["pr_refs"] == ["59"]
    assert group["work_item_provenance"] == {"issue_refs": ["25"], "pr_refs": ["59"]}


def test_explicit_map_is_the_only_capability_accounting_pass(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path)
    module = load_module()
    no_map = module.build_inventory(repo, fork_ref, upstream_ref, base)
    with_map = module.build_inventory(repo, fork_ref, upstream_ref, base, intent_map={fork_ref: "capability:explicit"})

    assert no_map["accounting"]["capability_accounting"]["complete"] is False
    assert with_map["accounting"]["capability_accounting"] == {"explicitly_accounted_commits": 1, "unaccounted_commits": [], "complete": True}
    assert with_map["provenance_buckets"][0]["id"] == "capability:explicit"


def test_incomplete_explicit_map_fails_accounting_gate(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "fork-work", fork_ref], check=True)
    extra = commit(repo, "second", "second.txt", "second\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "upstream"], check=True)
    module = load_module()
    inventory = module.build_inventory(repo, extra, upstream_ref, base, intent_map={fork_ref: "capability:explicit"})

    accounting = inventory["accounting"]["capability_accounting"]
    assert accounting["complete"] is False
    assert extra in accounting["unaccounted_commits"]


def test_evidence_policy_rejects_unsupported_drop():
    module = load_module()
    with pytest.raises(module.AuditInputError, match="DROP requires"):
        module._validate_evidence({"intents": {"capability:x": {"upstream_status": "FORK_ONLY", "disposition": "DROP"}}})


def test_semantic_status_requires_behavioral_evidence():
    module = load_module()
    with pytest.raises(module.AuditInputError, match="requires evidence"):
        module._validate_evidence({"intents": {"capability:x": {"upstream_status": "SEMANTIC_UPSTREAM", "disposition": "KEEP", "evidence": ["tree match"]}}})


def test_evidence_override_is_validated_and_rendered(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path, "retained behavior")
    module = load_module()
    evidence = {"intents": {"commit:" + fork_ref: {"upstream_status": "FORK_ONLY", "disposition": "KEEP", "confidence": "high", "evidence": ["current upstream lacks the behavior"], "behavioral_contracts": ["retained behavior remains user-visible"]}}}
    inventory = module.build_inventory(repo, fork_ref, upstream_ref, base, evidence["intents"])

    assert inventory["provenance_buckets"][0]["upstream_status"] == "FORK_ONLY"
    assert "retained behavior remains user-visible" in module.render_markdown(inventory)


def test_markdown_escapes_subject_and_file_delimiters(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path, "title | with `marker`")
    markdown = load_module().render_markdown(load_module().build_inventory(repo, fork_ref, upstream_ref, base))
    assert "title \\| with \\`marker\\`" in markdown


def test_explicit_intent_map_unknown_commit_is_rejected(tmp_path: Path):
    repo, base, fork_ref, upstream_ref = repo_with_branches(tmp_path)
    module = load_module()
    with pytest.raises(module.AuditInputError, match="outside the fork range"):
        module.build_inventory(repo, fork_ref, upstream_ref, base, intent_map={"0" * 40: "capability:missing"})
