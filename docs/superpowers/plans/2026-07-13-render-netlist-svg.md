# Render Netlist to SVG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `render-netlist` subcommand that flattens a `.kicad_sch` schematic's connectivity and renders it as an SVG via `netlistsvg`.

**Architecture:** New `kicad_skill/netlist_svg.py` builds a Yosys-style netlist JSON straight from `netlist_eval.extract_actual_netlist()`'s flattened net list (every pin already appears in some net, singleton or multi — no separate sheet re-walk needed), writes it to a temp file, and shells out to `npx --yes netlistsvg`. `main.py` gets a new subparser + handler following the existing lazy-import pattern (see `resolve`/`import-lib` handlers).

**Tech Stack:** Python (stdlib `subprocess`, `tempfile`, `json`), Node v22 via `npx` (already on PATH via nvm), `netlistsvg` (fetched on demand, not a project dependency).

**Deviation from spec note:** The spec (`docs/superpowers/specs/2026-07-13-render-netlist-svg-design.md`) says to reuse `_parse_sheet` separately to enumerate every component's full pin list. That's unnecessary: `extract_actual_netlist()` already unions **every** pin in the design into `pin_nodes` before grouping, so its return value (list of sets of `"Ref:Num"`) already contains singleton sets for unconnected pins. Cells (component ref → pin numbers) can be derived directly from that single list, with no second sheet-walk. This is a pure simplification — same JSON output, one less code path to maintain.

---

### Task 1: `build_yosys_netlist()` + test

**Files:**
- Create: `kicad_skill/netlist_svg.py`
- Test: `tests/test_netlist_svg.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_netlist_svg.py
import os
import unittest

from kicad_skill.netlist_eval import extract_actual_netlist
from kicad_skill.netlist_svg import build_yosys_netlist


class TestBuildYosysNetlist(unittest.TestCase):
    def setUp(self):
        base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.schematic = os.path.join(base, "mcp_test.kicad_sch")
        self.table = os.path.join(base, "sym-lib-table")

    def test_every_pin_appears_as_a_cell_port(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]
        actual_nets = extract_actual_netlist(self.schematic, self.table)

        expected_refs = {pid.split(":")[0] for net in actual_nets for pid in net}
        self.assertEqual(set(module["cells"].keys()), expected_refs)

        for net in actual_nets:
            for pid in net:
                ref, num = pid.split(":")
                self.assertIn(num, module["cells"][ref]["connections"])

    def test_multi_pin_nets_become_netnames_single_pin_nets_do_not(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]
        actual_nets = extract_actual_netlist(self.schematic, self.table)

        multi_pin_count = sum(1 for net in actual_nets if len(net) >= 2)
        self.assertEqual(len(module["netnames"]), multi_pin_count)

    def test_known_vdd_gnd_short_collapses_to_one_netname(self):
        # can_node fixture is a known-bad schematic: VDD and GND pins land on
        # a single electrical net (see tests/test_netlist_eval.py). The SVG
        # netlist must reproduce that merge, not silently split it.
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]

        vdd_gnd_pins = {"U1:5", "U1:6"}  # one VDD pin, one GND pin on MCU
        bits_seen = set()
        for ref_num in vdd_gnd_pins:
            ref, num = ref_num.split(":")
            for conn in module["cells"][ref]["connections"][num]:
                bits_seen.add(conn)
        # If they were still shorted, both pins resolve to the SAME net id.
        self.assertEqual(len(bits_seen), 1)

    def test_connection_ids_match_between_cell_and_netname(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]

        all_netname_bits = {bit for nn in module["netnames"].values() for bit in nn["bits"]}
        all_cell_bits = {
            bit
            for cell in module["cells"].values()
            for bits in cell["connections"].values()
            for bit in bits
        }
        self.assertTrue(all_netname_bits.issubset(all_cell_bits))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_netlist_svg -v`
Expected: `ModuleNotFoundError: No module named 'kicad_skill.netlist_svg'`

- [ ] **Step 3: Write the implementation**

```python
# kicad_skill/netlist_svg.py
"""Convert a schematic's flattened connectivity into netlistsvg's Yosys-style
netlist JSON, then render it to SVG via `npx netlistsvg`.

Every component reference becomes a generic labeled box (one port per pin,
keyed by pin number); every electrical net with 2+ pins becomes a wire. This
is a connectivity diagram, not a real schematic — no R/C/U symbol art.
"""
import json
import os
import subprocess
import tempfile

from .netlist_eval import extract_actual_netlist


def build_yosys_netlist(schematic_path, table_path=None):
    """Build the Yosys-style netlist JSON netlistsvg expects.

    Derives both cells (ref -> its pins) and nets directly from
    extract_actual_netlist()'s flattened output: every pin in the design
    already appears in some net there, singleton sets included for
    unconnected pins, so no separate pin enumeration pass is needed.
    """
    actual_nets = extract_actual_netlist(schematic_path, table_path)

    cells = {}  # ref -> {number -> None}, insertion order preserves discovery
    for net in actual_nets:
        for pid in net:
            ref, num = pid.split(":", 1)
            cells.setdefault(ref, {})[num] = None

    netnames = {}
    bit_by_pid = {}
    next_bit = 1
    for net in sorted(actual_nets, key=lambda s: sorted(s)[0]):
        if len(net) < 2:
            continue
        bit = next_bit
        next_bit += 1
        name = f"net{bit}"
        netnames[name] = {"bits": [bit]}
        for pid in net:
            bit_by_pid[pid] = bit

    cell_json = {}
    for ref, pins in cells.items():
        port_directions = {}
        connections = {}
        for num in pins:
            port_directions[num] = "inout"
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


def render_netlist_svg(schematic_path, output_path, table_path=None):
    """Build the netlist JSON and shell out to `npx netlistsvg` to render it.

    Errors propagate as-is (CalledProcessError / FileNotFoundError) — no
    message wrapping, matching fetch_easyeda_component's style.
    """
    netlist = build_yosys_netlist(schematic_path, table_path)

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(netlist, f)
        subprocess.run(["npx", "--yes", "netlistsvg", tmp_path, "-o", output_path], check=True)
    finally:
        os.remove(tmp_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_netlist_svg -v`
Expected: `OK` (4 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/netlist_svg.py tests/test_netlist_svg.py
git commit -m "feat: build netlistsvg-compatible netlist JSON from schematic connectivity"
```

---

### Task 2: `render_netlist_svg()` subprocess wrapper test

**Files:**
- Test: `tests/test_netlist_svg.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_netlist_svg.py`:

```python
import json
from unittest import mock


class TestRenderNetlistSvg(unittest.TestCase):
    def setUp(self):
        base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.schematic = os.path.join(base, "mcp_test.kicad_sch")
        self.table = os.path.join(base, "sym-lib-table")

    @mock.patch("kicad_skill.netlist_svg.subprocess.run")
    def test_invokes_npx_netlistsvg_and_cleans_up_temp_json(self, mock_run):
        from kicad_skill.netlist_svg import render_netlist_svg

        written_json = {}
        original_run = mock_run.side_effect

        def capture_and_check(cmd, check):
            self.assertEqual(cmd[:3], ["npx", "--yes", "netlistsvg"])
            self.assertEqual(cmd[-2], "-o")
            tmp_path = cmd[3]
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path) as f:
                written_json.update(json.load(f))

        mock_run.side_effect = capture_and_check

        render_netlist_svg(self.schematic, "/tmp/does_not_matter.svg", self.table)

        self.assertIn("modules", written_json)
        # temp file must be removed after the call, regardless of side_effect
        tmp_path = mock_run.call_args[0][0][3]
        self.assertFalse(os.path.exists(tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_netlist_svg.TestRenderNetlistSvg -v`
Expected: fails only if `render_netlist_svg` behaves wrong — since it's already implemented in Task 1, this should actually PASS immediately. Run it anyway to confirm; if it fails, the bug is in Step 3 of Task 1 (fix there, don't add new code here).

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_netlist_svg -v`
Expected: `OK` (5 tests total)

- [ ] **Step 4: Commit**

```bash
git add tests/test_netlist_svg.py
git commit -m "test: cover render_netlist_svg's npx invocation and temp-file cleanup"
```

---

### Task 3: Wire `render-netlist` into `main.py`

**Files:**
- Modify: `kicad_skill/main.py`

- [ ] **Step 1: Add the subparser**

In `kicad_skill/main.py`, immediately after the `fetch-easyeda` parser block (right before the `args = parser.parse_args()` line), add:

```python
    # render-netlist parser
    render_netlist_parser = subparsers.add_parser("render-netlist", help="Render a schematic's flattened netlist to SVG via netlistsvg")
    render_netlist_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    render_netlist_parser.add_argument("--output", required=True, help="Output .svg path")
    render_netlist_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")
```

- [ ] **Step 2: Add the dispatch branch**

In the `if args.command == ...` chain, right before the trailing `else:`, add:

```python
    elif args.command == 'render-netlist':
        from .netlist_svg import render_netlist_svg
        render_netlist_svg(args.schematic, args.output, args.table)
        print(f"Wrote {args.output}")
```

- [ ] **Step 3: Verify argparse wiring (no netlistsvg needed)**

Run: `uv run python -m kicad_skill.main render-netlist --help`
Expected: prints usage with `--schematic`, `--output`, `--table` flags, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add kicad_skill/main.py
git commit -m "feat: add render-netlist subcommand"
```

---

### Task 4: Document `render-netlist` in the skill reference

**Files:**
- Modify: `skills/kicad-helper/SKILL.md`

- [ ] **Step 1: Add a numbered entry**

Find the `### 7. Fetch & Import an EasyEDA/LCSC Component (fetch-easyeda)` section in `skills/kicad-helper/SKILL.md` and add a new section immediately after it (renumber if the file has since grown further sections — check the last existing number first with `grep -n '^### ' skills/kicad-helper/SKILL.md`):

```markdown
### 8. Render Netlist to SVG (`render-netlist`)
Flattens a schematic's actual connectivity (same logic `check-netlist` uses) and renders it as an SVG via [netlistsvg](https://github.com/nturley/netlistsvg). Each component becomes a generic labeled box with one port per pin (numbered); each electrical net becomes a wire. This is a connectivity diagram for debugging/docs, not a real schematic — no R/C/U symbol art.
```bash
/Users/ktchou/kicad-helper/kicad-helper render-netlist \
  --schematic "path/to/schematic.kicad_sch" \
  --output "netlist.svg"
```
* **Arguments:**
  - `--schematic`: Path to the `.kicad_sch` file.
  - `--output`: Output `.svg` path.
  - `--table`: Path to `sym-lib-table` (default: same folder as schematic).
* **Scope limits:** hierarchy is flattened into one diagram; assumes globally-unique component refs across the whole hierarchy; first run needs network access (`npx` fetches `netlistsvg` on demand, cached after).
```

- [ ] **Step 2: Read back to confirm no truncation**

Run: `grep -n "render-netlist" skills/kicad-helper/SKILL.md`
Expected: shows the new heading and both `render-netlist` command lines.

- [ ] **Step 3: Commit**

```bash
git add skills/kicad-helper/SKILL.md
git commit -m "docs: document render-netlist subcommand"
```

---

### Task 5: Manual end-to-end verification (real npx + netlistsvg)

This step needs network access (first `npx` run fetches the `netlistsvg` package) and is not part of the automated test suite — run it once by hand after Tasks 1-4 land.

- [ ] **Step 1: Run the CLI against the can_node fixture**

```bash
uv run python -m kicad_skill.main render-netlist \
  --schematic tests/fixtures/can_node/mcp_test.kicad_sch \
  --output /tmp/can_node_netlist.svg \
  --table tests/fixtures/can_node/sym-lib-table
```

Expected: prints `Wrote /tmp/can_node_netlist.svg`, exit code 0.

- [ ] **Step 2: Confirm the SVG is non-trivial**

```bash
ls -la /tmp/can_node_netlist.svg
grep -c '<svg' /tmp/can_node_netlist.svg
grep -o 'U1\|U2\|U3\|R1\|R2\|Y1\|C1\|C2\|C3\|C4\|J1' /tmp/can_node_netlist.svg | sort -u
```

Expected: file size > 0, exactly one `<svg` tag, and all 11 component refs (`U1`...`J1`) appear as text labels somewhere in the SVG.

- [ ] **Step 3: Run the full test suite one more time**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all tests pass, including the 5 new `test_netlist_svg` tests.

No commit for this task — it's verification only, nothing to check in.
