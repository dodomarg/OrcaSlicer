import json
import tempfile
from pathlib import Path

import local_preset_overlay as lp

PRINTER_A = {"name": "Printer A 0.4 nozzle", "is_system": True,
             "values": {"nozzle_diameter": "0.4", "printer_model": "A"}}
PRINTER_B = {"name": "Printer B 0.6 nozzle", "is_system": True,
             "values": {"nozzle_diameter": "0.6", "printer_model": "B"}}
PROCESS = {"name": "0.20mm Standard @A", "is_system": True,
           "values": {"outer_wall_line_width": "0.42", "print_extruder_id": "1", "print_extruder_variant": "Direct Drive Standard"}}
PRESETS = {"printer": [PRINTER_A, PRINTER_B], "process": [PROCESS], "filament": []}


def rule(**kw):
    base = {"id": "r", "name": "r", "target": "process", "apply_to": ["0.20mm*"], "priority": 0,
            "match": [{"key": "nozzle_diameter", "op": "eq", "value": "0.4"}],
            "set": {"outer_wall_line_width": "0.45"}}
    base.update(kw)
    return base


def test_match_and_generate():
    r = lp.plan({"rules": [rule()]}, PRESETS)
    assert len(r["items"]) == 1
    d = r["items"][0]["data"]
    assert d["inherits"] == PROCESS["name"]
    assert d["compatible_printers"] == [PRINTER_A["name"]]
    assert d["outer_wall_line_width"] == "0.45"
    assert d["print_extruder_variant"] == ["Direct Drive Standard"]


def test_numeric_ops():
    c = {"key": "nozzle_diameter", "op": "ge", "value": "0.5"}
    assert not lp.cond_match(c, PRINTER_A, PROCESS)
    assert lp.cond_match(c, PRINTER_B, PROCESS)


def test_priority_and_conflict():
    hi = rule(id="h", name="h", priority=5, set={"outer_wall_line_width": "0.5"})
    r = lp.plan({"rules": [rule(), hi]}, PRESETS)
    assert r["items"][0]["data"]["outer_wall_line_width"] == "0.5"
    twin = rule(id="t", name="t", set={"outer_wall_line_width": "0.46"})
    r = lp.plan({"rules": [rule(), twin]}, PRESETS)
    assert not r["items"] and r["conflicts"]


def test_vector_expansion():
    p = {"name": "Proc", "is_system": True, "values": {"x": "1;1"}}
    d = lp.plan({"rules": [rule(apply_to=["Proc"], set={"x": "2"})]}, {**PRESETS, "process": [p]})["items"][0]["data"]
    assert d["x"] == ["2", "2"]


def test_non_system_base_ignored():
    p = dict(PROCESS, is_system=False)
    assert not lp.plan({"rules": [rule()]}, {**PRESETS, "process": [p]})["items"]


def test_ip_printer():
    ip = {"name": "Garage A", "host": "192.168.1.5", "host_type": "moonraker", "base": PRINTER_A["name"]}
    r = lp.plan({"ip_printers": [ip, dict(ip, base="nope")]}, PRESETS)
    assert r["items"][0]["data"]["print_host"] == "192.168.1.5"
    assert len(r["items"]) == 1 and r["warnings"]


def test_write_bundle_removes_stale():
    with tempfile.TemporaryDirectory() as t:
        b = Path(t) / "local-preset-overlay"
        items = lp.plan({"rules": [rule()]}, PRESETS)["items"]
        assert lp.write_bundle(b, items) == 1
        meta = json.loads((b / "bundle_metadata.json").read_text())
        assert meta["id"] == b.name
        assert lp.write_bundle(b, []) == 0
        assert not list((b / "process").glob("*.json"))


def test_safe_filename():
    assert "/" not in lp.safe_filename("a/b:c")
    assert len(lp.safe_filename("x" * 300)) <= 120
