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
