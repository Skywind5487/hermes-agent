import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "fork_archaeology.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str, filename: str, content: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True)
    return git(repo, "rev-parse", "HEAD")


def test_inventory_accounts_fork_history_and_exact_absorption(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    commit(repo, "fork feature", "fork.txt", "fork\n")
    fork_ref = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    upstream_ref = commit(repo, "same feature upstream", "fork.txt", "fork\n")
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--fork-ref", fork_ref,
         "--upstream-ref", upstream_ref, "--merge-base", base, "--output-dir", str(out)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["accounting"]["complete"] is True
    assert inventory["intents"][0]["upstream_status"] == "EXACT_UPSTREAM"
    assert inventory["intents"][0]["disposition"] == "DROP"
    assert "Completeness: **PASS**" in (out / "inventory.md").read_text(encoding="utf-8")


def test_refs_must_be_full_shas(tmp_path: Path):
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("fork_archaeology", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    with pytest.raises(module.AuditInputError, match="full 40-character"):
        module.build_inventory(tmp_path, "main", "0" * 40, "0" * 40)


def test_pr_commits_are_grouped_and_mixed_patch_matches_are_partial(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    commit(repo, "feat: shared capability (#7)", "feature.txt", "same\n")
    fork_ref = commit(repo, "fix: follow-up for capability (#7)", "follow-up.txt", "fork-only\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    upstream_ref = commit(repo, "same capability upstream", "feature.txt", "same\n")

    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("fork_archaeology_grouped", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    inventory = module.build_inventory(repo, fork_ref, upstream_ref, base)

    assert [intent["id"] for intent in inventory["intents"]] == ["pr:7"]
    assert inventory["intents"][0]["upstream_status"] == "PARTIAL_UPSTREAM"
    assert inventory["intents"][0]["disposition"] == "SPLIT"
    assert len(inventory["intents"][0]["commits"]) == 2
    assert inventory["accounting"]["complete"] is True


def test_evidence_override_is_validated_and_rendered(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    fork_ref = commit(repo, "feat: retained behavior", "feature.txt", "fork\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    upstream_ref = commit(repo, "unrelated upstream", "other.txt", "other\n")

    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("fork_archaeology_evidence", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    evidence = {
        "intents": {
            "commit:" + git(repo, "rev-parse", fork_ref): {
                "upstream_status": "FORK_ONLY",
                "disposition": "KEEP",
                "confidence": "high",
                "evidence": ["current upstream lacks the behavior"],
                "behavioral_contracts": ["retained behavior remains user-visible"],
            }
        }
    }
    inventory = module.build_inventory(repo, fork_ref, upstream_ref, base, evidence["intents"])
    markdown = module.render_markdown(inventory)

    assert inventory["intents"][0]["upstream_status"] == "FORK_ONLY"
    assert inventory["intents"][0]["disposition"] == "KEEP"
    assert "retained behavior remains user-visible" in markdown
    assert "`FORK_ONLY`" in markdown


def test_explicit_intent_map_overrides_subject_grouping(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    base = commit(repo, "base", "base.txt", "base\n")
    fork_ref = commit(repo, "unrelated wording", "feature.txt", "fork\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream", base], check=True)
    upstream_ref = commit(repo, "unrelated upstream", "other.txt", "other\n")

    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("fork_archaeology_map", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    inventory = module.build_inventory(
        repo,
        fork_ref,
        upstream_ref,
        base,
        intent_map={git(repo, "rev-parse", fork_ref): "capability:explicit"},
    )

    assert inventory["intents"][0]["id"] == "capability:explicit"
