import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
ARTIFACTS = ROOT / "artifacts" / "fork-archaeology-issue-100"
COMPOSITION = ROOT / "artifacts" / "fork-feature-composition"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hard_dependency_has_cycle(plan):
    line_ids = {line["id"] for line in plan["feature_lines"]}
    graph = {line_id: [] for line_id in line_ids}
    for edge in plan["edges"]:
        if edge["type"] == "REQUIRES":
            graph[edge["from"]].append(edge["to"])

    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in line_ids)


def test_composition_plan_pins_phase1_and_current_upstream_inputs():
    plan = load(COMPOSITION / "feature-line-plan.json")
    assert plan["inputs"]["phase1_manifest_head"] == "f81cd921a89516d855b5b69906ce99e6351bc741"
    assert plan["inputs"]["current_upstream_main_sha"] == "56526bc0d36522ab7a87ee0056f70e3847d2f0e6"
    assert plan["inputs"]["current_fork_reconstruction_base"] == "fa5ed679cc6559c619038f327e6276f4b7e8d735"
    assert plan["inputs"]["current_fork_reconstruction_base_ref"] == "dev"


def test_every_phase1_capability_and_non_capability_is_composed():
    manifest = load(ARTIFACTS / "capability-manifest.json")
    plan = load(COMPOSITION / "feature-line-plan.json")
    expected_capabilities = {
        group["id"] for group in manifest["groups"] if group["record_class"] == "capability"
    }
    expected_non_capabilities = {
        group["id"] for group in manifest["groups"] if group["record_class"] == "non_capability"
    }
    assert {item["capability_id"] for item in plan["coverage"]} == expected_capabilities
    assert {item["group_id"] for item in plan["non_capability_accounting"]} == expected_non_capabilities
    assert len(plan["coverage"]) == 22
    assert len(plan["non_capability_accounting"]) == 4


def test_contract_clause_coverage_and_drop_ownership_are_explicit():
    plan = load(COMPOSITION / "feature-line-plan.json")
    line_ids = {line["id"] for line in plan["feature_lines"]}
    for item in plan["coverage"]:
        assert item["contract_clauses"]
        for clause in item["contract_clauses"]:
            assert clause["id"].startswith(item["capability_id"] + "#contract-")
            assert clause["coverage"] == item["composition_outcome"]
            assert set(clause["owner_line_ids"]) <= line_ids
        if item["composition_outcome"] == "UPSTREAM_OWNED":
            assert item["owner_line_ids"] == []
        else:
            assert item["owner_line_ids"]


def test_relationship_references_and_hard_dependency_dag_are_valid():
    plan = load(COMPOSITION / "feature-line-plan.json")
    line_ids = {line["id"] for line in plan["feature_lines"]}
    family_line_ids = {
        line_id
        for family in plan["feature_families"]
        for line_id in family["line_ids"]
    }
    assert family_line_ids == line_ids
    for edge in plan["edges"]:
        assert edge["from"] in line_ids
        assert edge["to"] in line_ids
        assert edge["type"] in {"REQUIRES", "EXTENDS", "SHARES_SUBSTRATE", "ORDER_AFTER"}
    assert not hard_dependency_has_cycle(plan)
    assert plan["verification"]["unresolved_composition_blockers"] == []
    assert plan["verification"]["phase1_drift"] == []


def test_composition_waves_reference_every_line_and_match_markdown():
    plan = load(COMPOSITION / "feature-line-plan.json")
    report = (COMPOSITION / "feature-line-plan.md").read_text(encoding="utf-8")
    feature_line_ids = {line["id"] for line in plan["feature_lines"]}
    wave_line_ids = []

    for wave in plan["composition_waves"]:
        ids = wave["line_ids"]
        assert set(ids) <= feature_line_ids
        assert len(ids) == len(set(ids))
        wave_line_ids.extend(ids)

        heading = f"### Wave {wave['wave']}"
        assert heading in report
        section = report.split(heading, 1)[1]
        section = section.split("### ", 1)[0]
        markdown_ids = re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE)
        assert markdown_ids == ids

    assert len(wave_line_ids) == len(set(wave_line_ids))
    assert set(wave_line_ids) == feature_line_ids


def test_feature_lines_have_acceptance_and_report_matches_machine_plan():
    plan = load(COMPOSITION / "feature-line-plan.json")
    report = (COMPOSITION / "feature-line-plan.md").read_text(encoding="utf-8")
    for line in plan["feature_lines"]:
        assert line["purpose"]
        assert line["acceptance"]
        assert line["landing_state"]
        assert f"`{line['id']}`" in report
    for item in plan["coverage"]:
        assert f"`{item['capability_id']}`" in report
    assert "Final hard-dependency blockers: **0**" in report


def test_ticket_projection_matches_hard_dependency_edges():
    plan = load(COMPOSITION / "feature-line-plan.json")
    expected = {
        line["id"]: set()
        for line in plan["feature_lines"]
    }
    for edge in plan["edges"]:
        if edge["type"] == "REQUIRES":
            expected[edge["from"]].add(edge["to"])
    actual = {
        item["line_id"]: set(item["depends_on"])
        for item in plan["ticket_projection"]
    }
    assert actual == expected
