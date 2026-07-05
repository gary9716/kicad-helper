# Regenerate-from-Ground-Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a clean **flat** KiCad schematic from a user-confirmed ground-truth netlist such that `netlist_eval` certifies **0 short / 0 open**.

**Architecture:** Load GT (nets + new `components` block) → place symbols (reuse `place_symbols_and_resolve`) → classify each net as label-routed (power/global, ≥3 pins, or non-adjacent) vs wire-routed (2-pin adjacent) → emit local labels at pin coordinates for label-nets and short A\* wires for wire-nets → verify with `netlist_eval` → on any short/open move offending wire-nets to label-route and regenerate (all-labels config is structurally short-proof, so the loop converges).

**Tech Stack:** Python, KiCad S-expression files, `unittest`, `uv`. Reuses `kicad_skill/{schematic,module,parser,netlist_eval}.py`.

**Key simplification vs spec:** label-routing emits a local `label` **at the pin's own connection coordinate** — no stub wire. KiCad joins same-named labels and joins a label sitting on a pin endpoint, so there is zero trunk wire and zero collinear-merge risk. The spec's "stub + label" intent (no shared trunk) is preserved with less geometry.

---

### Task 1: Extend the demo ground-truth with a `components` block

**Files:**
- Modify: `scratch/mcp_test/can_node.groundtruth.json`

This is the input the generator consumes. `lib_id`/`value` from the demo build harness (`scratch/run_mcp_verification.py`). Placement coords copied from that harness so the regenerated layout matches the intended one; the generator will still re-resolve overlaps.

- [ ] **Step 1: Add the `components` block**

Insert a top-level `"components"` key into `scratch/mcp_test/can_node.groundtruth.json` (keep `nets` unchanged):

```json
  "components": {
    "U1": {"lib_id": "mcp_test:MCU",      "value": "MCU",      "x": 74.93,  "y": 100.33, "angle": 0},
    "U2": {"lib_id": "mcp_test:MCP2515",  "value": "MCP2515",  "x": 124.46, "y": 100.33, "angle": 0},
    "U3": {"lib_id": "mcp_test:TJA1050",  "value": "TJA1050",  "x": 165.10, "y": 100.33, "angle": 0},
    "R1": {"lib_id": "Device:R",          "value": "10k",      "x": 124.46, "y": 74.93,  "angle": 0},
    "R2": {"lib_id": "Device:R",          "value": "120",      "x": 185.42, "y": 85.09,  "angle": 0},
    "Y1": {"lib_id": "Device:Crystal",    "value": "16MHz",    "x": 124.46, "y": 124.46, "angle": 0},
    "C1": {"lib_id": "Device:C",          "value": "22pF",     "x": 114.30, "y": 137.16, "angle": 0},
    "C2": {"lib_id": "Device:C",          "value": "22pF",     "x": 134.62, "y": 137.16, "angle": 0},
    "C3": {"lib_id": "Device:C",          "value": "0.1uF",    "x": 104.14, "y": 85.09,  "angle": 0},
    "C4": {"lib_id": "Device:C",          "value": "0.1uF",    "x": 144.78, "y": 85.09,  "angle": 0},
    "J1": {"lib_id": "Connector_Generic:Conn_01x04", "value": "CONN_CAN", "x": 209.55, "y": 100.33, "angle": 0}
  }
```

- [ ] **Step 2: Verify the JSON still parses**

Run: `uv run python -c "import json; d=json.load(open('scratch/mcp_test/can_node.groundtruth.json')); print(len(d['components']), 'components', len(d['nets']), 'nets')"`
Expected: `11 components 13 nets`

- [ ] **Step 3: Commit**

```bash
git add scratch/mcp_test/can_node.groundtruth.json
git commit -m "feat(groundtruth): add components block for regenerate-from-GT"
```

---

### Task 2: GT loader + validation

**Files:**
- Create: `kicad_skill/regenerate.py`
- Test: `tests/test_regenerate.py`

Loads the extended GT and validates that every ref used in a net has a component entry (fail loud — Rule 12).

- [ ] **Step 1: Write the failing test**

Create `tests/test_regenerate.py`:

```python
import json
import os
import unittest

from kicad_skill.regenerate import load_gt_components


class TestLoadGtComponents(unittest.TestCase):
    def test_missing_component_for_net_pin_raises(self):
        gt = {
            "nets": [{"name": "VDD", "pins": ["U1:1", "U9:1"]}],
            "components": {"U1": {"lib_id": "x:Y", "value": "v"}},
        }
        with self.assertRaises(ValueError) as cm:
            load_gt_components(gt)
        self.assertIn("U9", str(cm.exception))

    def test_valid_gt_returns_nets_and_components(self):
        gt = {
            "nets": [{"name": "VDD", "pins": ["U1:1", "U2:1"]}],
            "components": {
                "U1": {"lib_id": "x:Y", "value": "v"},
                "U2": {"lib_id": "x:Z", "value": "w"},
            },
        }
        nets, comps = load_gt_components(gt)
        self.assertEqual(len(nets), 1)
        self.assertEqual(set(comps), {"U1", "U2"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_regenerate -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kicad_skill.regenerate'`

- [ ] **Step 3: Write minimal implementation**

Create `kicad_skill/regenerate.py`:

```python
"""Regenerate a clean flat schematic from a ground-truth netlist.

See docs/superpowers/specs/2026-06-16-regenerate-from-gt-design.md.
Pin identity is "Ref:PinNumber", matching the ground-truth JSON.
"""
import json
import os
import sys
import uuid

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.schematic import (
    place_symbols_and_resolve,
    connect_symbols_in_schematic,
    load_sym_lib_table,
    find_symbol_definition,
)
from kicad_skill.module import get_symbol_pins_global
from kicad_skill.netlist_eval import extract_actual_netlist, compare, load_ground_truth


def load_gt_components(gt):
    """Return (nets, components) after validating every net ref has a component."""
    nets = gt["nets"]
    components = gt.get("components", {})
    used = {pin.split(":")[0] for net in nets for pin in net["pins"]}
    missing = sorted(r for r in used if r not in components)
    if missing:
        raise ValueError(f"components block missing entries for refs: {', '.join(missing)}")
    return nets, components
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_regenerate -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/regenerate.py tests/test_regenerate.py
git commit -m "feat(regenerate): GT loader with component validation"
```

---

### Task 3: Net classification (pure)

**Files:**
- Modify: `kicad_skill/regenerate.py`
- Test: `tests/test_regenerate.py`

`classify_nets(nets, centers)` splits nets into `(label_nets, wire_nets)`. `centers` is `{ref: (x, y)}`. A net is wire-routed only when it has exactly 2 pins on two components whose centers are within `ADJ_THRESHOLD` mm; otherwise label-routed. Power-named nets are always label-routed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regenerate.py`:

```python
from kicad_skill.regenerate import classify_nets


class TestClassifyNets(unittest.TestCase):
    def setUp(self):
        self.centers = {
            "U1": (0, 0), "U2": (5, 0),     # adjacent
            "U3": (200, 0),                  # far away
            "R2": (6, 0),                    # adjacent to U2
        }

    def test_power_named_net_is_label_routed(self):
        nets = [{"name": "GND", "pins": ["U1:1", "U2:1"]}]  # 2-pin, adjacent, but power
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["GND"])
        self.assertEqual(wire, [])

    def test_three_pin_net_is_label_routed(self):
        nets = [{"name": "SIG", "pins": ["U1:2", "U2:2", "R2:1"]}]
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["SIG"])

    def test_two_pin_adjacent_net_is_wire_routed(self):
        nets = [{"name": "OSC1", "pins": ["U2:7", "R2:1"]}]
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in wire], ["OSC1"])
        self.assertEqual(label, [])

    def test_two_pin_distant_net_is_label_routed(self):
        nets = [{"name": "TX", "pins": ["U1:3", "U3:1"]}]  # U1 near origin, U3 far
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["TX"])
        self.assertEqual(wire, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_regenerate.TestClassifyNets -v`
Expected: FAIL — `ImportError: cannot import name 'classify_nets'`

- [ ] **Step 3: Write minimal implementation**

Append to `kicad_skill/regenerate.py`:

```python
import math

ADJ_THRESHOLD = 30.0  # mm; two components closer than this can share a short wire
POWER_NAMES = {"VDD", "VCC", "VSS", "GND", "VBUS", "V+", "V-", "3V3", "5V", "GNDA", "VDDA"}


def _is_power(name):
    u = name.upper()
    return u in POWER_NAMES or u.startswith("GND") or u.startswith("VDD") or u.startswith("VCC")


def classify_nets(nets, centers):
    """Split nets into (label_nets, wire_nets). See module docstring / spec."""
    label_nets, wire_nets = [], []
    for net in nets:
        pins = net["pins"]
        refs = [p.split(":")[0] for p in pins]
        wireable = False
        if not _is_power(net["name"]) and len(pins) == 2 and refs[0] != refs[1]:
            a, b = centers.get(refs[0]), centers.get(refs[1])
            if a and b and math.dist(a, b) < ADJ_THRESHOLD:
                wireable = True
        (wire_nets if wireable else label_nets).append(net)
    return label_nets, wire_nets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_regenerate.TestClassifyNets -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/regenerate.py tests/test_regenerate.py
git commit -m "feat(regenerate): net classification (label vs adjacent-wire)"
```

---

### Task 4: Schematic scaffolding + label emission

**Files:**
- Modify: `kicad_skill/regenerate.py`
- Test: `tests/test_regenerate.py`

`_write_blank_schematic(path)` creates an empty `kicad_sch`. `_pin_coords(sch_path, table_path)` returns `{("Ref","Num"): (x, y)}` for every placed symbol pin (post-placement actual coords). `_emit_labels(sch_path, label_nets, pin_coords)` inserts a local `label` at each pin coordinate of each label-net. Label correctness is proven by feeding the result through `netlist_eval`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regenerate.py`:

```python
import tempfile
import shutil
from kicad_skill.regenerate import (
    _write_blank_schematic, _pin_coords, _emit_labels,
)


class TestLabelEmission(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "..", "scratch", "mcp_test")
        self.table = os.path.join(self.base, "sym-lib-table")
        if not os.path.exists(self.table):
            self.skipTest("mcp_test artifacts not present")
        self.tmp = tempfile.mkdtemp()
        # work inside the project dir so ${KIPRJMOD} relative libs resolve
        self.sch = os.path.join(self.base, "_tmp_label_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.sch) and os.remove(self.sch))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_labels_join_two_pins_into_one_net(self):
        _write_blank_schematic(self.sch)
        place_symbols_and_resolve(self.sch, self.table, [
            {"lib_id": "mcp_test:MCU", "reference": "U1", "value": "MCU",
             "x": 80, "y": 80, "angle": 0.0},
            {"lib_id": "mcp_test:MCP2515", "reference": "U2", "value": "MCP2515",
             "x": 140, "y": 80, "angle": 0.0},
        ], margin=2.54, resolve=True)
        coords = _pin_coords(self.sch, self.table)
        # U1:5 (3V3) and U2:18 (VDD) should both exist
        self.assertIn(("U1", "5"), coords)
        self.assertIn(("U2", "18"), coords)
        _emit_labels(self.sch, [{"name": "VDD", "pins": ["U1:5", "U2:18"]}], coords)
        actual = extract_actual_netlist(self.sch, self.table)
        rep = compare(actual, [{"name": "VDD", "pins": ["U1:5", "U2:18"]}])
        self.assertFalse(rep["fatal"], rep)
        self.assertEqual(rep["opens"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_regenerate.TestLabelEmission -v`
Expected: FAIL — `ImportError: cannot import name '_write_blank_schematic'`

- [ ] **Step 3: Write minimal implementation**

Append to `kicad_skill/regenerate.py`:

```python
def _write_blank_schematic(path):
    blank = [
        "kicad_sch",
        ["version", "20211123"],
        ["generator", "eeschema"],
        ["generator_version", "10.0"],
        ["uuid", str(uuid.uuid4())],
        ["paper", "A4"],
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(blank))


def _pin_coords(sch_path, table_path):
    """Return {(ref, number): (x, y)} for every placed symbol pin."""
    project_dir = os.path.dirname(os.path.abspath(sch_path))
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    local_defs = {}
    for child in sexpr[1:]:
        if isinstance(child, list) and child and child[0] == "lib_symbols":
            for d in child[1:]:
                if isinstance(d, list) and d[0] == "symbol" and len(d) > 1:
                    local_defs[d[1]] = d
    coords = {}
    for child in sexpr[1:]:
        if not isinstance(child, list) or not child or child[0] != "symbol":
            continue
        ref, lib_id = "", ""
        for sub in child[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == "lib_id":
                    lib_id = sub[1]
                elif sub[0] == "property" and len(sub) > 2 and sub[1] == "Reference":
                    ref = sub[2]
        defn = local_defs.get(lib_id)
        if not defn and ":" in lib_id:
            lib_name, sym_name = lib_id.split(":", 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        for p in get_symbol_pins_global(child, defn):
            coords[(ref, p["number"])] = (p["x"], p["y"])
    return coords


def _make_label(name, x, y):
    return [
        "label", name,
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left", "bottom"]],
        ["uuid", str(uuid.uuid4())],
    ]


def _emit_labels(sch_path, label_nets, pin_coords):
    """Insert a local label at each pin coordinate of each label-net."""
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    for net in label_nets:
        for pin in net["pins"]:
            ref, num = pin.split(":")
            xy = pin_coords.get((ref, num))
            if xy is None:
                raise ValueError(f"pin {pin} not found among placed symbols")
            sexpr.append(_make_label(net["name"], xy[0], xy[1]))
    with open(sch_path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(sexpr))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_regenerate.TestLabelEmission -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/regenerate.py tests/test_regenerate.py
git commit -m "feat(regenerate): blank schematic, pin-coord map, label emission"
```

---

### Task 5: `regenerate_schematic` orchestrator + fallback loop

**Files:**
- Modify: `kicad_skill/regenerate.py`
- Test: `tests/test_regenerate.py`

Ties it together: place → classify → emit labels + wire adjacents → verify → on short/open, demote offending wire-nets to labels and retry. Integration test regenerates the real MCP2515 demo and asserts `netlist_eval` reports 0 short / 0 open and the output is flat.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regenerate.py`:

```python
from kicad_skill.regenerate import regenerate_schematic


class TestRegenerateIntegration(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "..", "scratch", "mcp_test")
        self.table = os.path.join(self.base, "sym-lib-table")
        self.gt = os.path.join(self.base, "can_node.groundtruth.json")
        if not os.path.exists(self.table) or not os.path.exists(self.gt):
            self.skipTest("mcp_test artifacts not present")
        self.out = os.path.join(self.base, "_regen_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.out) and os.remove(self.out))

    def test_regenerated_schematic_has_no_short_or_open(self):
        out_path, rep = regenerate_schematic(self.gt, self.table, self.out)
        self.assertFalse(rep["fatal"], f"report still fatal: {rep}")
        self.assertEqual(rep["shorts"], [])
        self.assertEqual(rep["opens"], [])

    def test_regenerated_schematic_is_flat(self):
        regenerate_schematic(self.gt, self.table, self.out)
        with open(self.out) as f:
            content = f.read()
        self.assertNotIn("(sheet ", content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_regenerate.TestRegenerateIntegration -v`
Expected: FAIL — `ImportError: cannot import name 'regenerate_schematic'`

- [ ] **Step 3: Write minimal implementation**

Append to `kicad_skill/regenerate.py`:

```python
def _placements_from_components(components):
    placements = []
    for ref, c in sorted(components.items()):
        placements.append({
            "lib_id": c["lib_id"],
            "reference": ref,
            "value": c.get("value", ""),
            "x": float(c.get("x", 0.0)),
            "y": float(c.get("y", 0.0)),
            "angle": float(c.get("angle", 0.0)),
        })
    # Auto-row fallback for components without coords: spread along a row.
    col = 0
    for p in placements:
        if p["x"] == 0.0 and p["y"] == 0.0:
            p["x"] = 50.0 + col * 25.4
            p["y"] = 100.0
            col += 1
    return placements


def regenerate_schematic(gt_path, table_path, out_sch, max_iter=None):
    """Build a clean flat schematic from the ground-truth netlist.

    Returns (out_sch, report). Raises if a fatal report survives the
    all-labels configuration (GT/geometry contradiction).
    """
    gt = load_ground_truth(gt_path)
    nets, components = load_gt_components(gt)
    placements = _placements_from_components(components)

    forced_labels = set()  # net names demoted from wire to label after a short/open
    if max_iter is None:
        max_iter = len(nets) + 1

    for _ in range(max_iter):
        _write_blank_schematic(out_sch)
        place_symbols_and_resolve(out_sch, table_path, placements, margin=2.54, resolve=True)
        coords = _pin_coords(out_sch, table_path)
        centers = _centers_from_schematic(out_sch)

        label_nets, wire_nets = classify_nets(nets, centers)
        # Apply demotions from previous iterations.
        demoted = [n for n in wire_nets if n["name"] in forced_labels]
        wire_nets = [n for n in wire_nets if n["name"] not in forced_labels]
        label_nets = label_nets + demoted

        _emit_labels(out_sch, label_nets, coords)
        for net in wire_nets:
            a, b = net["pins"]
            connect_symbols_in_schematic(out_sch, table_path, [{"from": a, "to": b}], orthogonal=True)

        actual = extract_actual_netlist(out_sch, table_path)
        rep = compare(actual, nets)
        if not rep["fatal"]:
            return out_sch, rep

        # Demote every wire-net implicated in a short/open and retry.
        offenders = {n for s in rep["shorts"] for n in s["gt_nets"]}
        offenders |= {o["gt_net"] for o in rep["opens"]}
        wire_names = {n["name"] for n in wire_nets}
        newly = offenders & wire_names
        if not newly:
            raise RuntimeError(f"regenerate failed: fatal report not fixable by label demotion: {rep}")
        forced_labels |= newly

    raise RuntimeError("regenerate did not converge")


def _centers_from_schematic(sch_path):
    """Return {ref: (x, y)} symbol placement centers from a schematic."""
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    centers = {}
    for child in sexpr[1:]:
        if not isinstance(child, list) or not child or child[0] != "symbol":
            continue
        ref, at = "", None
        for sub in child[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == "property" and len(sub) > 2 and sub[1] == "Reference":
                    ref = sub[2]
                elif sub[0] == "at":
                    at = (float(sub[1]), float(sub[2]))
        if ref and at:
            centers[ref] = at
    return centers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_regenerate.TestRegenerateIntegration -v`
Expected: PASS (2 tests). If a short survives, the loop demotes wire-nets to labels until clean.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `uv run python -m unittest discover -s tests -q 2>&1 | grep -E "^(OK|FAILED|Ran)"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add kicad_skill/regenerate.py tests/test_regenerate.py
git commit -m "feat(regenerate): orchestrator with label-fallback convergence loop"
```

---

### Task 6: CLI `regenerate-from-gt`

**Files:**
- Modify: `kicad_skill/regenerate.py`

- [ ] **Step 1: Add the CLI main**

Append to `kicad_skill/regenerate.py`:

```python
def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="regenerate-from-gt",
        description="Generate a clean flat schematic from a ground-truth netlist.")
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    out_path, rep = regenerate_schematic(args.ground_truth, args.table, args.out)
    status = "FATAL" if rep["fatal"] else "CLEAN"
    print(f"[{status}] wrote {out_path}")
    print(f"  GT nets: {len(rep['ok'])} ok, "
          f"{len(rep['shorts'])} shorts, {len(rep['opens'])} opens, "
          f"{len(rep['missing'])} missing pins")
    return 1 if rep["fatal"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the CLI end-to-end**

Run:
```bash
cd /Users/ktchou/kicad-helper
uv run kicad_skill/regenerate.py --ground-truth scratch/mcp_test/can_node.groundtruth.json --table scratch/mcp_test/sym-lib-table --out scratch/mcp_test/can_node_clean.kicad_sch
```
Expected: `[CLEAN] wrote ...` and exit 0.

- [ ] **Step 3: Cross-check with netlist_eval CLI**

Run:
```bash
cd /Users/ktchou/kicad-helper
uv run kicad_skill/netlist_eval.py --schematic scratch/mcp_test/can_node_clean.kicad_sch --ground-truth scratch/mcp_test/can_node.groundtruth.json --table scratch/mcp_test/sym-lib-table
```
Expected: `VERDICT: OK`, 0 shorts, 0 opens.

- [ ] **Step 4: Commit**

```bash
git add kicad_skill/regenerate.py
git commit -m "feat(regenerate): regenerate-from-gt CLI"
```

---

## Notes for the implementer

- Run everything from `/Users/ktchou/kicad-helper` with `uv run`. Tests that touch `scratch/mcp_test` skip cleanly if the demo artifacts are absent — they are present on this branch.
- `place_symbols_and_resolve` may shift symbols to clear overlaps, so always read pin/center coords **after** placement (`_pin_coords`, `_centers_from_schematic`), never from the components block.
- The fallback loop's safety rests on: an all-labels configuration has no trunk wires, so `extract_actual_netlist` cannot merge two nets. If the all-labels config still reports fatal, the GT itself is inconsistent (e.g. two pins at the same coordinate) — raising is correct (Rule 12, fail loud).
- Do not commit `scratch/mcp_test/can_node_clean.kicad_sch` or `_*_test.kicad_sch` scratch outputs unless asked.
