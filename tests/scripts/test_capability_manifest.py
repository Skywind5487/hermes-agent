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
    # The archaeology runner owns one-to-one provenance buckets.  The reviewed
    # manifest may additionally contain hunk-level behavioral overlay groups.
    assert set(evidence) <= group_ids
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


def test_memory_trim_policy_and_diagnostics_are_separate_capabilities():
    manifest = load("capability-manifest.json")
    intent_map = load("intent-map.json")["commits"]
    groups = {group["id"]: group for group in manifest["groups"]}

    policy = groups["capability:memory-trim-policy"]
    diagnostics = groups["capability:memory-trim-diagnostics"]
    assert intent_map["04f1af72be078cef69de538f1519f93a73088b0d"] == "capability:memory-trim-diagnostics"
    assert intent_map["a9d2b9af4f800fef23fa7ecaf2ea270b43e326eb"] == "capability:memory-trim-policy"
    assert policy["status"] == "PARTIAL_UPSTREAM"
    assert policy["disposition"] == "PORT"
    assert policy["confidence"] == "high"
    assert policy["historical_commits"][0]["sha"] == "a9d2b9af4f800fef23fa7ecaf2ea270b43e326eb"
    assert policy["supporting_historical_commits"][0]["sha"] == "04f1af72be078cef69de538f1519f93a73088b0d"
    assert diagnostics["status"] == "PARTIAL_UPSTREAM"
    assert diagnostics["disposition"] == "KEEP"
    assert diagnostics["historical_commits"][0]["sha"] == "04f1af72be078cef69de538f1519f93a73088b0d"


def test_behavioral_audit_covers_all_changes_and_merge_events():
    audit = load("behavioral-coverage-audit.json")
    assert audit["coverage_gate"] == {
        "change_records": 145,
        "merge_events": 26,
        "unique_change_records_reviewed": 145,
        "behavior_rows": 141,
        "promoted_behavior_rows": 5,
        "explicit_nonsemantic_rows": 8,
        "uncovered_behavior_rows": 0,
        "complete": True,
    }
    assert len(audit["change_records"]) == 149
    assert len({row["record_sha"] for row in audit["change_records"]}) == 145
    assert len(audit["merge_events"]) == 26
    promoted = {
        row["proposed_capability"]
        for row in audit["change_records"]
        if row["action"] == "PROMOTE_TO_SEPARATE_CAPABILITY"
    }
    assert promoted == {
        "capability:compression-session-boundary",
        "capability:memory-trim-policy",
        "capability:sqlite-write-contention-policy",
        "capability:session-search-context-hydration",
    }


def test_behavioral_overlays_keep_provenance_bucket_accounting_separate():
    manifest = load("capability-manifest.json")
    groups = {group["id"]: group for group in manifest["groups"]}
    assert groups["capability:compression-session-boundary"]["supporting_historical_commits"][0]["sha"] == "176646d2cd6c95fa49b9414f21ed9e781b0aaa84"
    assert groups["capability:sqlite-write-contention-policy"]["supporting_historical_commits"][0]["sha"] == "cc2531fbc6df8fdb34fca0b096b798eb53f970dd"
    assert groups["capability:session-search-context-hydration"]["supporting_historical_commits"][0]["sha"] == "b879cbd332b8ac66ada6aaebfc5a9e61ae6cbe70"
    assert groups["capability:lifecycle-sqlite-telemetry"]["behavioral_overlays"] == ["capability:compression-session-boundary"]
    assert groups["non-capability:incidental-hardening"]["behavioral_overlays"] == ["capability:sqlite-write-contention-policy"]
    assert groups["non-capability:performance-validation"]["behavioral_overlays"] == ["capability:session-search-context-hydration"]
