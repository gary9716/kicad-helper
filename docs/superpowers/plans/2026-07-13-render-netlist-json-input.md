# Render Netlist v2 (GT JSON Input) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `render-netlist` takes a ground-truth netlist JSON (`--netlist`) instead of a `.kicad_sch` (`--schematic`/`--table` removed), and names diagram nets by their GT names.

**Architecture:** Rework `kicad_skill/netlist_svg.py` into a pure JSON→SVG transform (no schematic parsing, no sym-lib-table); update `main.py` subparser; rewrite `tests/test_netlist_svg.py` against the GT fixture. Spec: `docs/superpowers/specs/2026-07-13-render-netlist-json-input-design.md`.

**Verified facts (2026-07-13):** `netlist_eval.load_ground_truth(path)` is a bare `json.load` — no validation; GT fixture at `tests/fixtures/can_node/can_node.groundtruth.json` has 13 nets (`VDD` 7 pins, `GND` 9 pins, 11 two-pin, `OSC1`/`OSC2`/`CANH`/`CANL` 3 pins) covering refs U1-U3, R1, R2, Y1, C1-C4, J1. Breaking change is user-approved.

---

### Task 1: Rework `netlist_svg.py` + tests

**Files:**
- Modify: `kicad_skill/netlist_svg.py` (full rewrite of both functions)
- Modify: `tests/test_netlist_svg.py` (full rewrite)

- [ ] **Step 1: Rewrite the test file** (replaces all existing content — v1 tests test the removed schematic path)

```python
# tests/test_netlist_svg.py
import json
import os
import unittest
from unittest import mock

from kicad_skill.netlist_svg import build_yosys_netlist, render_netlist_svg

FIXTURE_GT = os.path.join(os.path.dirname(__file__), "fixtures", "can_node",
                          "can_node.groundtruth.json")


def load_gt_nets():
    with open(FIXTURE_GT, encoding="utf-8") as f:
        return json.load(f)["nets"]


class TestBuildYosysNetlist(unittest.TestCase):
    def setUp(self):
        self.nets = load_gt_nets()

    def test_every_pin_appears_as_a_cell_port(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        expected_refs = {p.split(":")[0] for n in self.nets for p in n["pins"]}
        self.assertEqual(set(module["cells"].keys()), expected_refs)
        for n in self.nets:
            for pid in n["pins"]:
                ref, num = pid.split(":")
                self.assertIn(num, module["cells"][ref]["connections"])

    def test_netnames_use_gt_names(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        gt_names = {n["name"] for n in self.nets if len(n["pins"]) >= 2}
        self.assertEqual(set(module["netnames"].keys()), gt_names)

    def test_single_pin_nets_render_port_but_no_netname(self):
        nets = [{"name": "SIG", "pins": ["U9:1", "U8:2"]},
                {"name": "NC", "pins": ["U9:3"]}]
        module = build_yosys_netlist(nets)["modules"]["top"]
        self.assertIn("3", module["cells"]["U9"]["connections"])
        self.assertEqual(module["cells"]["U9"]["connections"]["3"], [])
        self.assertEqual(set(module["netnames"]), {"SIG"})

    def test_duplicate_net_names_are_uniquified(self):
        nets = [{"name": "N", "pins": ["A:1", "B:1"]},
                {"name": "N", "pins": ["C:1", "D:1"]}]
        module = build_yosys_netlist(nets)["modules"]["top"]
        self.assertEqual(len(module["netnames"]), 2)
        self.assertIn("N", module["netnames"])

    def test_connection_ids_match_between_cell_and_netname(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        netname_bits = {b for nn in module["netnames"].values() for b in nn["bits"]}
        cell_bits = {b for c in module["cells"].values()
                     for bits in c["connections"].values() for b in bits}
        self.assertTrue(netname_bits.issubset(cell_bits))
        # every net's pins share exactly its bit
        by_name = {n["name"]: n["pins"] for n in self.nets if len(n["pins"]) >= 2}
        for name, pins in by_name.items():
            bit = module["netnames"][name]["bits"][0]
            for pid in pins:
                ref, num = pid.split(":")
                self.assertEqual(module["cells"][ref]["connections"][num], [bit])


class TestRenderNetlistSvg(unittest.TestCase):
    @mock.patch("kicad_skill.netlist_svg.subprocess.run")
    def test_invokes_npx_netlistsvg_and_cleans_up_temp_json(self, mock_run):
        written = {}

        def capture(cmd, check):
            self.assertEqual(cmd[:3], ["npx", "--yes", "netlistsvg"])
            self.assertEqual(cmd[-2], "-o")
            with open(cmd[3]) as f:
                written.update(json.load(f))
        mock_run.side_effect = capture

        render_netlist_svg(FIXTURE_GT, "/tmp/out.svg")

        self.assertIn("modules", written)
        self.assertIn("VDD", written["modules"]["top"]["netnames"])
        tmp = mock_run.call_args[0][0][3]
        self.assertFalse(os.path.exists(tmp))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_netlist_svg -v`
Expected: TypeError/failures — old implementation takes `(schematic_path, table_path)`.

- [ ] **Step 3: Rewrite the implementation**

```python
# kicad_skill/netlist_svg.py
"""Render a ground-truth netlist JSON to an SVG connectivity diagram via
`npx netlistsvg`.

Input is the GT netlist format used by check-netlist/regenerate
({"nets": [{"name", "pins": ["Ref:Num", ...]}], ...}); only nets[].name and
nets[].pins are consumed. Every component reference becomes a generic labeled
box (one port per pin, keyed by pin number); every net with 2+ pins becomes a
wire carrying its GT name. Connectivity diagram, not a real schematic.
"""
import json
import os
import subprocess
import tempfile


def build_yosys_netlist(gt_nets):
    """GT nets ([{"name", "pins"}]) -> Yosys-style netlist JSON for netlistsvg.

    Netnames keyed by GT net name (duplicates uniquified with _2, _3, ...);
    single-pin nets contribute ports but no netname (dangling stub).
    """
    cells = {}  # ref -> {number -> None}
    for net in gt_nets:
        for pid in net["pins"]:
            ref, num = pid.split(":", 1)
            cells.setdefault(ref, {})[num] = None

    netnames = {}
    bit_by_pid = {}
    next_bit = 1
    for net in gt_nets:
        if len(net["pins"]) < 2:
            continue
        name = net["name"]
        suffix = 2
        while name in netnames:
            name = f'{net["name"]}_{suffix}'
            suffix += 1
        bit = next_bit
        next_bit += 1
        netnames[name] = {"bits": [bit]}
        for pid in net["pins"]:
            bit_by_pid[pid] = bit

    cell_json = {}
    for ref, pins in cells.items():
        port_directions = {}
        connections = {}
        for num in pins:
            # netlistsvg's JSON schema only allows "input"/"output"; direction
            # isn't tracked in GT netlists, so "input" uniformly.
            port_directions[num] = "input"
            pid = f"{ref}:{num}"
            connections[num] = [bit_by_pid[pid]] if pid in bit_by_pid else []
        cell_json[ref] = {
            "type": "generic",
            "port_directions": port_directions,
            "connections": connections,
        }

    return {
        "modules": {
            "top": {
                "ports": {},
                "cells": cell_json,
                "netnames": netnames,
            }
        }
    }


def render_netlist_svg(netlist_path, output_path):
    """Load a GT netlist JSON and shell out to `npx netlistsvg`.

    Errors propagate as-is (CalledProcessError / FileNotFoundError /
    KeyError on malformed JSON) — no message wrapping.
    """
    with open(netlist_path, encoding="utf-8") as f:
        gt_nets = json.load(f)["nets"]
    netlist = build_yosys_netlist(gt_nets)

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(netlist, f)
        subprocess.run(["npx", "--yes", "netlistsvg", tmp_path, "-o", output_path], check=True)
    finally:
        os.remove(tmp_path)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_netlist_svg -v` → OK (6 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/netlist_svg.py tests/test_netlist_svg.py
git commit -m "feat!: render-netlist takes ground-truth netlist JSON, names nets

BREAKING: build_yosys_netlist/render_netlist_svg no longer accept a
schematic path; input is the GT netlist JSON used by check-netlist."
```

---

### Task 2: CLI + docs

**Files:**
- Modify: `kicad_skill/main.py` (render-netlist subparser + dispatch)
- Modify: `skills/kicad-helper/SKILL.md` (§8)

- [ ] **Step 1: Replace the subparser** — in `kicad_skill/main.py`, replace the three `render_netlist_parser.add_argument` lines:

```python
    # render-netlist parser
    render_netlist_parser = subparsers.add_parser("render-netlist", help="Render a ground-truth netlist JSON to SVG via netlistsvg")
    render_netlist_parser.add_argument("--netlist", required=True, help="Path to the ground-truth netlist JSON")
    render_netlist_parser.add_argument("--output", required=True, help="Output .svg path")
```

- [ ] **Step 2: Update dispatch branch**:

```python
    elif args.command == 'render-netlist':
        from .netlist_svg import render_netlist_svg
        render_netlist_svg(args.netlist, args.output)
        print(f"Wrote {args.output}")
```

- [ ] **Step 3: Rewrite SKILL.md §8** — keep heading `### 8. Render Netlist to SVG (`render-netlist`)`; new body:

```markdown
Renders a ground-truth netlist JSON (the format used by `check-netlist` and the `plan-ground-truth-netlist` skill) as an SVG connectivity diagram via [netlistsvg](https://github.com/nturley/netlistsvg). Each component becomes a generic labeled box with one port per pin; each net with 2+ pins becomes a wire labeled with its net name. Connectivity diagram for debugging/docs, not a real schematic.
```bash
/Users/ktchou/kicad-helper/kicad-helper render-netlist \
  --netlist "path/to/design.groundtruth.json" \
  --output "netlist.svg"
```
* **Arguments:**
  - `--netlist`: Path to the ground-truth netlist JSON.
  - `--output`: Output `.svg` path.
* **Note:** only have a schematic? Build the GT JSON first via the `plan-ground-truth-netlist` skill. First run needs network (`npx` fetches `netlistsvg`, cached after).
```
(Match surrounding sections' formatting; don't include the outer fence.)

- [ ] **Step 4: Verify**

Run: `uv run python -m kicad_skill.main render-netlist --help` → shows `--netlist`/`--output` only, exit 0.
Run: `uv run python -m unittest discover -s tests` → all pass.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/main.py skills/kicad-helper/SKILL.md
git commit -m "feat!: render-netlist CLI takes --netlist JSON, drop --schematic/--table"
```

---

### Task 3: Manual e2e (no commit)

- [ ] **Step 1:**

```bash
uv run python -m kicad_skill.main render-netlist \
  --netlist tests/fixtures/can_node/can_node.groundtruth.json \
  --output /tmp/gt_netlist.svg
grep -c '<svg' /tmp/gt_netlist.svg
grep -o 'VDD\|SPI_CS\|CANH' /tmp/gt_netlist.svg | sort -u
```
Expected: `Wrote /tmp/gt_netlist.svg`; one `<svg`; net names `VDD`/`SPI_CS`/`CANH` appear as text in the SVG (do NOT Read the SVG — grep only).
