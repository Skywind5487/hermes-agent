import json
from pathlib import Path


ARTIFACTS = Path(__file__).parents[2] / "artifacts" / "fork-archaeology-issue-100"


def load(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def test_phase_one_manifest_accounts_for_every_frozen_record():
    manifest = load("capability-manifest.json")
    inventory = load("inventory.json")
    intent_map = load("intent-map.json")

    assert manifest["inputs"] == {
        "fork_ref": "35c8564c9c0af3d75bcbdf1d793e7207e5528f06",
        "upstream_ref": "460d345642ee3d143a3e461abe39fd42b86a7e54",
        "merge_base": "91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53",
    }
    assert inventory["accounting"]["discovery_coverage"]["complete"] is True
    assert inventory["accounting"]["capability_accounting"]["complete"] is True
    assert inventory["accounting"]["capability_accounting"]["unaccounted_commits"] == []

    records = [
        item["sha"]
        for group in inventory["provenance_buckets"]
        for item in group["commits"] + group["merge_events"]
    ]
    assert len(records) == 171
    assert len(set(records)) == 171
    assert set(records) == set(intent_map["commits"])
    assert manifest["accounting"] == {
        "historical_change_records": 145,
        "merge_events": 26,
        "total_records": 171,
        "mapped_records": 171,
        "complete": True,
    }


def test_manifest_groups_have_review_fields_and_explicit_non_capability_class():
    manifest = load("capability-manifest.json")
    evidence = load("evidence.json")["intents"]
    required = {
        "capability", "intent_name", "historical_commits", "historical_issues", "historical_prs",
        "merge_provenance", "behavioral_contracts", "current_dev_survival",
        "upstream_prior_art_current_implementation", "status", "disposition", "confidence",
        "unresolved_questions", "source_inventory_records",
    }
    statuses = {"EXACT_UPSTREAM", "SEMANTIC_UPSTREAM", "PARTIAL_UPSTREAM", "FORK_ONLY", "LOST_IN_FORK", "NEEDS_REVIEW"}
    dispositions = {"DROP", "KEEP", "PORT", "SPLIT", "NEEDS_REVIEW"}

    group_ids = {group["id"] for group in manifest["groups"]}
    assert group_ids == set(evidence)
    for group in manifest["groups"]:
        assert required <= group.keys()
        assert group["status"] in statuses
        assert group["disposition"] in dispositions
        assert group["behavioral_contracts"]
        assert group["unresolved_questions"]
        assert group["change_record_count"] + group["merge_event_count"] == len(group["historical_commits"]) + len(group["merge_provenance"])
        if group["id"].startswith("non-capability:"):
            assert group["record_class"] == "non_capability"
        else:
            assert group["record_class"] == "capability"
        if group["status"] in {"EXACT_UPSTREAM", "SEMANTIC_UPSTREAM"}:
            assert evidence[group["id"]]["evidence"]
            assert evidence[group["id"]]["behavioral_contracts"]
