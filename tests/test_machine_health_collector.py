import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "machine_health_collector.py"


def _module():
    spec = importlib.util.spec_from_file_location("machine_health_collector", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cpu_delta_preserves_busy_and_iowait_invariants():
    module = _module()
    before = {"user": 10, "system": 5, "idle": 80, "iowait": 5}
    after = {"user": 20, "system": 10, "idle": 100, "iowait": 10}
    result = module._cpu_delta(before, after, 1.0)
    assert result["total_jiffies"] == 40
    assert result["busy_percent"] == 37.5
    assert result["iowait_percent"] == 12.5


def test_disk_delta_reports_root_and_io_fields(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "_root_device", lambda: "vda1")
    before = {"vda1": {"reads_completed": 1, "sectors_read": 2, "writes_completed": 3, "sectors_written": 4, "io_in_progress": 0, "io_ticks_ms": 5, "weighted_io_ms": 6}}
    after = {"vda1": {"reads_completed": 2, "sectors_read": 6, "writes_completed": 4, "sectors_written": 10, "io_in_progress": 1, "io_ticks_ms": 8, "weighted_io_ms": 12}}
    result = module._disk_delta(before, after, 1.0)
    assert result["root_device"] == "vda1"
    assert result["root"]["sectors_read"] == 4
    assert result["root"]["sectors_written"] == 6
    assert result["root"]["io_ticks_ms"] == 3
    assert result["root"]["weighted_io_ms"] == 6


def test_collect_contains_machine_identity_resources_and_db_sizes(monkeypatch, tmp_path):
    module = _module()
    db = tmp_path / "state.db"
    db.write_bytes(b"db")
    monkeypatch.setattr(module, "STATE_DB", db)
    monkeypatch.setattr(module, "SAMPLE_SECONDS", 0.0)
    record = module.collect()
    assert record["event"] == "MACHINE_HEALTH"
    assert record["correlation"]["request_id"] is None
    assert record["machine_wall_time"]
    assert record["machine_monotonic_ns"] > 0
    assert record["boot_id"]
    assert record["hostname"]
    assert record["cpu_count"] >= 1
    assert record["memory"]["total_bytes"] > 0
    assert record["state_db"]["state_db_size_bytes"] == 2
    assert "root_device" in record["disk"]
    assert record["process"]["pid"] > 0
    assert record["process"]["pid_starttime"] > 0
    assert "read_bytes" in record["process"]["io"]
    assert "write_bytes" in record["process"]["io"]
    assert "cpu" in record["pressure"]
    assert "io" in record["pressure"]
    assert "memory" in record["pressure"]
    assert isinstance(record["collector_errors"], dict)


def test_machine_collector_prefers_gateway_process_over_tmux_wrapper():
    module = _module()
    candidates = [
        (1175531, "tmux new-session -d -s hermes hermes gateway run"),
        (1175532, "/venv/bin/python3 /usr/local/bin/hermes gateway run"),
    ]
    assert module._select_hermes_pid(candidates) == 1175532


def test_machine_collector_distinguishes_unavailable_pressure(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "_read_text_status", lambda path: ("", None))
    assert module._pressure_snapshot()["cpu"] is None
    assert module._pressure_snapshot()["io"] is None
    assert module._pressure_snapshot()["memory"] is None


def test_machine_collector_reports_pressure_read_errors_separately(monkeypatch):
    module = _module()
    monkeypatch.setattr(
        module,
        "_read_text_status",
        lambda path: ("", "PermissionError"),
    )
    pressure, errors = module._pressure_snapshot_with_errors()
    assert pressure["io"] is None
    assert errors["io"] == "PermissionError"


def test_main_appends_one_json_record(monkeypatch, tmp_path, capsys):
    module = _module()
    output = tmp_path / "machine-health.jsonl"
    monkeypatch.setattr(module, "LOG_PATH", output)
    monkeypatch.setattr(module, "SAMPLE_SECONDS", 0.0)
    assert module.main() == 0
    line = output.read_text().strip()
    record = json.loads(line)
    stdout = json.loads(capsys.readouterr().out)
    assert record["event"] == "MACHINE_HEALTH"
    assert stdout["machine_monotonic_ns"] == record["machine_monotonic_ns"]
    assert output.read_text().count("\n") == 1
