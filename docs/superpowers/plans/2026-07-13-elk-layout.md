# ELK Auto-Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New `elk-layout` subcommand that re-places and re-routes a `.kicad_sch` via elkjs (`layered` + `FIXED_POS` ports + orthogonal edges), plus `regenerate --routing elk`.

**Architecture:** `kicad_skill/elk_layout.py` (parse-reuse → classify → ELK JSON → node subprocess → grid snap → write-back) + `tools/elk_runner.js` (stdin JSON → elkjs → stdout JSON). Spec: `docs/superpowers/specs/2026-07-13-elk-layout-design.md`.

**Tech Stack:** Python stdlib, elkjs via node (v22 present), existing modules: `resolve_layout._extract_symbols`/`_move_symbol`, `netlist_eval.extract_actual_netlist`/`compare`, `regenerate._is_power`/`_make_label`/`_label_orientation`/`_centers_from_schematic`, `erc.run_erc`/`find_kicad_cli`, `evaluate_layout.evaluate_schematic_layout`.

**Verified API facts (from source, 2026-07-13):**
- `_extract_symbols(sch_sexpr, local_defs, lib_map, project_dir)` → list of dicts `{ref, lib_id, x, y, angle, mirror_x, mirror_y, local_bbox, bbox, pin_pts, at_node, prop_at_nodes}`; `bbox` is a `BoundingBox(xmin, ymin, xmax, ymax)`; internally already computes `pins = get_symbol_pins_global(node, defn)` (each pin: `{number, name, type, x, y}` — absolute coords) but does NOT store it (Task 2 adds it).
- `_move_symbol(sym, dx, dy)` moves the live sexpr (`at_node`, `prop_at_nodes`) AND the dict/bbox/pin_pts.
- `extract_actual_netlist(root_path, table_path)` → list of sets of `"Ref:Num"` (singletons included).
- `run_erc(sch_path)` → `{"violations", "error_count", "ok"}`; `find_kicad_cli()` → path or None.
- `evaluate_schematic_layout(sch_path, table_path)` → dict with `"score"`, `"fatal"`, etc.
- `_is_power(name)`: upper() in POWER_NAMES or startswith GND/VDD/VCC.
- `_make_label(name, x, y, angle=0, justify="left")` → label sexpr; `_label_orientation(px, py, cx, cy)` → `(angle, justify)`.
- KiCad schematic y-axis increases downward — same as ELK. No y-flip needed.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/elk_runner.js` (new) | stdin ELK JSON → elkjs layout → stdout JSON. ~15 lines. |
| `tools/package.json` (new) | pins `elkjs` dependency. `npm install --prefix tools/` one-time setup. |
| `kicad_skill/elk_layout.py` (new) | classify nets, build ELK graph, run node, snap, write-back, orchestrator. |
| `kicad_skill/resolve_layout.py` (modify) | `_extract_symbols` additionally stores `'pins'` (already computed internally). |
| `kicad_skill/regenerate.py` (modify) | `routing="elk"` mode delegates to elk_layout after label-only build. |
| `kicad_skill/main.py` (modify) | `elk-layout` subparser + dispatch; `--routing` choices gain `elk`. |
| `tests/test_elk_layout.py` (new) | unit tests (no node needed) + gated integration test. |
| `skills/kicad-helper/SKILL.md` (modify) | new numbered entry. |

---

### Task 1: Node runner (`tools/elk_runner.js` + `tools/package.json`)

**Files:**
- Create: `tools/elk_runner.js`
- Create: `tools/package.json`
- Modify: `.gitignore` (add `tools/node_modules/`)

- [ ] **Step 1: Write the runner**

```javascript
// tools/elk_runner.js
// Reads an ELK JSON graph on stdin, runs elkjs layout, writes layouted JSON to stdout.
// Usage: node tools/elk_runner.js < graph.json > layouted.json
const ELK = require('elkjs');
const elk = new ELK();

let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  elk.layout(JSON.parse(input))
    .then((result) => { process.stdout.write(JSON.stringify(result)); })
    .catch((err) => {
      process.stderr.write(String(err && err.stack ? err.stack : err));
      process.exit(1);
    });
});
```

- [ ] **Step 2: Write package.json**

```json
{
  "name": "kicad-helper-elk-runner",
  "private": true,
  "dependencies": {
    "elkjs": "^0.9.3"
  }
}
```

- [ ] **Step 3: Add gitignore entry**

Append to `.gitignore`:
```
tools/node_modules/
tools/package-lock.json
```

- [ ] **Step 4: Install and smoke-test manually**

Run:
```bash
npm install --prefix tools/
echo '{"id":"root","layoutOptions":{"elk.algorithm":"layered"},"children":[{"id":"a","width":10,"height":10},{"id":"b","width":10,"height":10}],"edges":[{"id":"e1","sources":["a"],"targets":["b"]}]}' | node tools/elk_runner.js
```
Expected: JSON on stdout containing `"x":` and `"y":` coordinates for nodes `a` and `b`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add tools/elk_runner.js tools/package.json .gitignore
git commit -m "feat: add elkjs node runner for schematic auto-layout"
```

---

### Task 2: Store `pins` in `_extract_symbols` output

**Files:**
- Modify: `kicad_skill/resolve_layout.py` (the `symbols.append({...})` dict near the end of `_extract_symbols`)
- Test: `tests/test_elk_layout.py` (new file, first test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_elk_layout.py
import os
import unittest

from kicad_skill.parser import parse_sexpr
from kicad_skill.resolve_layout import _extract_symbols
from kicad_skill.schematic import load_sym_lib_table

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
SCH = os.path.join(FIXTURE, "mcp_test.kicad_sch")
TABLE = os.path.join(FIXTURE, "sym-lib-table")


def load_fixture_symbols():
    with open(SCH, encoding="utf-8") as f:
        sch = parse_sexpr(f.read())
    local_defs = {}
    for child in sch[1:]:
        if isinstance(child, list) and child[0] == "lib_symbols":
            for sym in child[1:]:
                if isinstance(sym, list) and sym[0] == "symbol" and len(sym) > 1:
                    local_defs[sym[1]] = sym
    lib_map = load_sym_lib_table(TABLE)
    return sch, _extract_symbols(sch, local_defs, lib_map, FIXTURE)


class TestExtractSymbolsPins(unittest.TestCase):
    def test_symbols_carry_full_pin_dicts(self):
        _, symbols = load_fixture_symbols()
        by_ref = {s["ref"]: s for s in symbols}
        self.assertIn("U2", by_ref)  # MCP2515, 18 pins
        u2 = by_ref["U2"]
        self.assertIn("pins", u2)
        self.assertEqual(len(u2["pins"]), 18)
        p = u2["pins"][0]
        for key in ("number", "name", "type", "x", "y"):
            self.assertIn(key, p)
        # pins agree with the pin_pts the function already exposed
        self.assertEqual(
            sorted((pp["x"], pp["y"]) for pp in u2["pins"]),
            sorted(u2["pin_pts"]),
        )


if __name__ == "__main__":
    unittest.main()
```

Note: verify the import path for `load_sym_lib_table` — it is used by `resolve_layout.py`; check its import there (`grep -n "load_sym_lib_table" kicad_skill/resolve_layout.py`) and import from the same module in the test. If it lives elsewhere (e.g. `kicad_skill.schematic`), adjust the test import accordingly.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout -v`
Expected: FAIL — `AssertionError: 'pins' not found in ...` (the dict lacks the key).

- [ ] **Step 3: Implement — one-line addition**

In `kicad_skill/resolve_layout.py`, inside `_extract_symbols`, the final `symbols.append({...})` dict: add `'pins': pins,` alongside the existing `'pin_pts': pin_pts,` entry (the local `pins` variable is already computed a few lines above via `get_symbol_pins_global(node, defn) if defn else []`).

- [ ] **Step 4: Run tests — new one passes, no regressions**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (1 test)
Run: `uv run python -m unittest discover -s tests -v` → all pass (97 existing + 1).

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/resolve_layout.py tests/test_elk_layout.py
git commit -m "feat: expose full pin dicts from _extract_symbols"
```

---

### Task 3: Net classification (`classify_for_elk`)

**Files:**
- Create: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_elk_layout.py`)

```python
from kicad_skill.elk_layout import classify_for_elk


class TestClassifyForElk(unittest.TestCase):
    def test_power_fanout_and_singleton_rules(self):
        nets = [
            ("VDD", {"U1:5", "U2:18", "C3:1"}),      # power name -> label
            ("GND_A", {"U1:6", "C3:2"}),              # startswith GND -> label
            ("SPI_SCK", {"U1:4", "U2:13", "U3:1", "J1:2"}),  # fanout 4 -> label
            ("TXCAN", {"U2:1", "U3:1"}),              # 2-pin signal -> edge
            ("OSC1", {"U2:8", "Y1:1", "C1:1"}),       # 3-pin signal -> edge
            ("NC_PIN", {"U2:11"}),                    # singleton -> neither
        ]
        edge_nets, label_nets = classify_for_elk(nets, fanout_threshold=4)
        self.assertEqual({n for n, _ in edge_nets}, {"TXCAN", "OSC1"})
        self.assertEqual({n for n, _ in label_nets}, {"VDD", "GND_A", "SPI_SCK"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestClassifyForElk -v`
Expected: `ModuleNotFoundError: No module named 'kicad_skill.elk_layout'`

- [ ] **Step 3: Implement**

```python
# kicad_skill/elk_layout.py
"""ELK (elkjs) based schematic auto-layout.

Pipeline: parse (reuse resolve_layout/_netlist_eval) -> classify nets
(power & high-fanout -> labels, 2-3 pin signals -> ELK edges) -> build ELK
JSON (layered, FIXED_POS ports, orthogonal edges) -> node tools/elk_runner.js
-> snap to KiCad grid -> write symbols/wires/labels/junctions back.

Spec: docs/superpowers/specs/2026-07-13-elk-layout-design.md
"""
import json
import os
import subprocess

from .regenerate import _is_power

GRID = 1.27  # KiCad wire/pin grid (mm)


def classify_for_elk(nets, fanout_threshold=4):
    """Split named nets into (edge_nets, label_nets).

    nets: iterable of (name, set_of_pin_ids). Power-named nets and nets with
    fanout >= threshold become labels; 2..threshold-1 pin signal nets become
    ELK edges; singletons are dropped (nothing to draw).
    """
    edge_nets, label_nets = [], []
    for name, pins in nets:
        if len(pins) < 2:
            continue
        if _is_power(name) or len(pins) >= fanout_threshold:
            label_nets.append((name, pins))
        else:
            edge_nets.append((name, pins))
    return edge_nets, label_nets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout net classification (power/fanout -> labels)"
```

---

### Task 4: Net naming for existing schematics

`extract_actual_netlist` returns anonymous pin-sets; classification needs names (power detection). Derive a name per net: prefer an existing label on the net's pins' positions, else synthesize `NET_<first-pin>`.

**Files:**
- Modify: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
from kicad_skill.elk_layout import name_nets


class TestNameNets(unittest.TestCase):
    def test_label_name_wins_else_synthesized(self):
        nets = [{"U1:5", "U2:18"}, {"U2:1", "U3:1"}]
        # pin_positions: pin id -> (x, y)
        pin_positions = {
            "U1:5": (10.16, 20.32), "U2:18": (50.8, 20.32),
            "U2:1": (50.8, 30.48), "U3:1": (90.17, 30.48),
        }
        # labels_at: (x, y) -> name  (from existing label/global_label sexprs)
        labels_at = {(10.16, 20.32): "VDD"}
        named = name_nets(nets, pin_positions, labels_at)
        by_pins = {frozenset(p): n for n, p in named}
        self.assertEqual(by_pins[frozenset({"U1:5", "U2:18"})], "VDD")
        synth = by_pins[frozenset({"U2:1", "U3:1"})]
        self.assertTrue(synth.startswith("NET_"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestNameNets -v`
Expected: ImportError (`name_nets` not defined).

- [ ] **Step 3: Implement** (append to `kicad_skill/elk_layout.py`)

```python
def name_nets(nets, pin_positions, labels_at):
    """Attach a name to each anonymous pin-set net.

    A net whose any pin position carries an existing label uses that label's
    text; otherwise the name is synthesized from the first pin id (sorted),
    e.g. NET_U2_1. Returns list of (name, pin_set).
    """
    named = []
    for net in nets:
        name = None
        for pid in sorted(net):
            pos = pin_positions.get(pid)
            if pos is not None and pos in labels_at:
                name = labels_at[pos]
                break
        if name is None:
            name = "NET_" + sorted(net)[0].replace(":", "_")
        named.append((name, net))
    return named


def collect_labels_at(sch_sexpr):
    """{(x, y): label_text} for every label/global_label in the sheet."""
    out = {}
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child and child[0] in ("label", "global_label"):
            at = next((s for s in child[1:]
                       if isinstance(s, list) and s[0] == "at" and len(s) > 2), None)
            if at is not None:
                out[(float(at[1]), float(at[2]))] = child[1]
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout net naming from existing labels"
```

---

### Task 5: ELK graph builder (`build_elk_graph`)

**Files:**
- Modify: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
from kicad_skill.elk_layout import build_elk_graph


class TestBuildElkGraph(unittest.TestCase):
    def test_graph_structure_from_fixture(self):
        _, symbols = load_fixture_symbols()
        edge_nets = [("TXCAN", {"U2:1", "U3:1"})]
        graph = build_elk_graph(symbols, edge_nets)

        self.assertEqual(graph["layoutOptions"]["elk.algorithm"], "layered")
        self.assertEqual(graph["layoutOptions"]["elk.direction"], "RIGHT")
        self.assertEqual(graph["layoutOptions"]["elk.edgeRouting"], "ORTHOGONAL")

        nodes = {n["id"]: n for n in graph["children"]}
        self.assertEqual(set(nodes), {s["ref"] for s in symbols})

        u2 = nodes["U2"]
        self.assertEqual(u2["layoutOptions"]["elk.portConstraints"], "FIXED_POS")
        self.assertGreater(u2["width"], 0)
        self.assertGreater(u2["height"], 0)
        ports = {p["id"]: p for p in u2["ports"]}
        self.assertIn("U2:1", ports)
        p = ports["U2:1"]
        # port coords are relative to node origin (bbox min corner), inside node
        self.assertGreaterEqual(p["x"], 0)
        self.assertLessEqual(p["x"], u2["width"])
        self.assertIn(p["layoutOptions"]["elk.port.side"],
                      ("WEST", "EAST", "NORTH", "SOUTH"))

        self.assertEqual(len(graph["edges"]), 1)
        e = graph["edges"][0]
        self.assertEqual(set(e["sources"] + e["targets"]), {"U2:1", "U3:1"})

    def test_port_side_matches_pin_position(self):
        _, symbols = load_fixture_symbols()
        graph = build_elk_graph(symbols, [])
        by_ref = {s["ref"]: s for s in symbols}
        for node in graph["children"]:
            sym = by_ref[node["id"]]
            b = sym["bbox"]
            for port in node["ports"]:
                pin = next(p for p in sym["pins"]
                           if f'{sym["ref"]}:{p["number"]}' == port["id"])
                dists = {
                    "WEST": pin["x"] - b.xmin, "EAST": b.xmax - pin["x"],
                    "NORTH": pin["y"] - b.ymin, "SOUTH": b.ymax - pin["y"],
                }
                self.assertEqual(port["layoutOptions"]["elk.port.side"],
                                 min(dists, key=dists.get))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestBuildElkGraph -v`
Expected: ImportError (`build_elk_graph` not defined).

- [ ] **Step 3: Implement** (append to `kicad_skill/elk_layout.py`)

```python
def _port_side(pin_x, pin_y, bbox):
    """Closest bbox edge wins. KiCad y grows downward, same as ELK: the
    bbox ymin edge is the visual top -> NORTH."""
    dists = {
        "WEST": pin_x - bbox.xmin,
        "EAST": bbox.xmax - pin_x,
        "NORTH": pin_y - bbox.ymin,
        "SOUTH": bbox.ymax - pin_y,
    }
    return min(dists, key=dists.get)


def build_elk_graph(symbols, edge_nets):
    """symbols: _extract_symbols output (with 'pins'). edge_nets: [(name, pins)].

    Node origin = bbox min corner; ports relative to it; FIXED_POS so ELK
    never moves a pin. Spacing values are mm (ELK is unitless).
    """
    children = []
    for sym in symbols:
        b = sym["bbox"]
        ports = []
        for p in sym["pins"]:
            ports.append({
                "id": f'{sym["ref"]}:{p["number"]}',
                "x": p["x"] - b.xmin,
                "y": p["y"] - b.ymin,
                "width": 0.1,
                "height": 0.1,
                "layoutOptions": {"elk.port.side": _port_side(p["x"], p["y"], b)},
            })
        children.append({
            "id": sym["ref"],
            "width": b.xmax - b.xmin,
            "height": b.ymax - b.ymin,
            "ports": ports,
            "layoutOptions": {"elk.portConstraints": "FIXED_POS"},
        })

    edges = []
    for i, (name, pins) in enumerate(edge_nets):
        ordered = sorted(pins)
        edges.append({
            "id": f"e{i}_{name}",
            "sources": [ordered[0]],
            "targets": ordered[1:],
        })

    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": 5.08,
            "elk.spacing.edgeNode": 2.54,
            "elk.layered.spacing.nodeNodeBetweenLayers": 10.16,
        },
        "children": children,
        "edges": edges,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (5 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout graph builder with FIXED_POS ports"
```

---

### Task 6: Node subprocess wrapper (`run_elk`)

**Files:**
- Modify: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
import json as _json
from unittest import mock

from kicad_skill.elk_layout import run_elk


class TestRunElk(unittest.TestCase):
    @mock.patch("kicad_skill.elk_layout.subprocess.run")
    def test_pipes_graph_json_through_node_runner(self, mock_run):
        graph = {"id": "root", "children": []}
        mock_run.return_value = mock.Mock(stdout=_json.dumps({"id": "root", "x": 0}))
        result = run_elk(graph)
        self.assertEqual(result["id"], "root")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "node")
        self.assertTrue(cmd[1].endswith(os.path.join("tools", "elk_runner.js")))
        kwargs = mock_run.call_args[1]
        self.assertEqual(_json.loads(kwargs["input"]), graph)
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestRunElk -v`
Expected: ImportError (`run_elk` not defined).

- [ ] **Step 3: Implement** (append to `kicad_skill/elk_layout.py`)

```python
_RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tools", "elk_runner.js")


def run_elk(graph):
    """Pipe the graph through `node tools/elk_runner.js`.

    Errors propagate bare (FileNotFoundError if node missing,
    CalledProcessError carrying elkjs stderr) — same style as render-netlist.
    One-time setup: `npm install --prefix tools/`.
    """
    proc = subprocess.run(
        ["node", _RUNNER],
        input=json.dumps(graph),
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (6 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout node subprocess wrapper"
```

---

### Task 7: Grid snap + wire derivation (`snap_deltas`, `derive_wires`)

**Files:**
- Modify: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
from kicad_skill.elk_layout import snap_deltas, derive_wires


class TestSnapAndDerive(unittest.TestCase):
    def test_deltas_snapped_to_grid(self):
        _, symbols = load_fixture_symbols()
        # fake ELK result: every node shifted to a float origin
        layouted = {"children": [
            {"id": s["ref"], "x": 3.1, "y": 7.9} for s in symbols
        ]}
        deltas = snap_deltas(layouted, symbols)
        for ref, (dx, dy) in deltas.items():
            self.assertAlmostEqual(dx % 1.27, 0.0, places=6,
                                   msg=f"{ref} dx {dx} off grid")
            self.assertAlmostEqual(dy % 1.27, 0.0, places=6)

    def test_derive_wires_endpoints_are_exact_pins_and_orthogonal(self):
        # one edge U2:1 -> U3:1 with a float ELK route
        moved_pins = {"U2:1": (50.8, 30.48), "U3:1": (91.44, 40.64)}
        elk_edges = [{
            "id": "e0_TXCAN",
            "sources": ["U2:1"], "targets": ["U3:1"],
            "sections": [{
                "startPoint": {"x": 50.79, "y": 30.5},
                "endPoint": {"x": 91.4, "y": 40.6},
                "bendPoints": [{"x": 70.1, "y": 30.5}, {"x": 70.1, "y": 40.6}],
            }],
        }]
        wires = derive_wires(elk_edges, moved_pins)
        # flatten all segments
        pts = set()
        for seg in wires:
            (x1, y1), (x2, y2) = seg
            pts.add((x1, y1)); pts.add((x2, y2))
            # orthogonal
            self.assertTrue(x1 == x2 or y1 == y2, f"diagonal segment {seg}")
            # on grid
            for v in (x1, y1, x2, y2):
                self.assertAlmostEqual(v % 1.27, 0.0, places=6)
        # exact pin endpoints present
        self.assertIn((50.8, 30.48), pts)
        self.assertIn((91.44, 40.64), pts)

    def test_snap_breaking_orthogonality_inserts_jog(self):
        # start/end pins whose snap forces a non-collinear join
        moved_pins = {"A:1": (0.0, 0.0), "B:1": (5.08, 2.54)}
        elk_edges = [{
            "id": "e0_X",
            "sources": ["A:1"], "targets": ["B:1"],
            "sections": [{
                "startPoint": {"x": 0.0, "y": 0.0},
                "endPoint": {"x": 5.0, "y": 2.5},
                "bendPoints": [],
            }],
        }]
        wires = derive_wires(elk_edges, moved_pins)
        for (x1, y1), (x2, y2) in wires:
            self.assertTrue(x1 == x2 or y1 == y2)
        # path connects A to B
        pts = {p for seg in wires for p in seg}
        self.assertIn((0.0, 0.0), pts)
        self.assertIn((5.08, 2.54), pts)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestSnapAndDerive -v`
Expected: ImportError.

- [ ] **Step 3: Implement** (append to `kicad_skill/elk_layout.py`)

```python
def _snap(v):
    return round(v / GRID) * GRID


def snap_deltas(layouted, symbols):
    """{ref: (dx, dy)} — grid-snapped translation for each symbol.

    ELK node origin corresponds to the symbol's bbox min corner. Snapping the
    DELTA (not the absolute position) preserves every intra-symbol alignment:
    pins that were on-grid stay on-grid.
    """
    by_ref = {s["ref"]: s for s in symbols}
    deltas = {}
    for node in layouted.get("children", []):
        sym = by_ref.get(node["id"])
        if sym is None:
            continue
        b = sym["bbox"]
        deltas[node["id"]] = (_snap(node["x"] - b.xmin), _snap(node["y"] - b.ymin))
    return deltas


def _orthogonalize(points):
    """Insert an L-jog wherever two consecutive points differ in both axes."""
    out = [points[0]]
    for pt in points[1:]:
        px, py = out[-1]
        x, y = pt
        if px != x and py != y:
            out.append((x, py))
        if (x, y) != out[-1]:
            out.append((x, y))
    return out


def derive_wires(elk_edges, moved_pins):
    """ELK edge sections -> KiCad wire segments [( (x1,y1), (x2,y2) ), ...].

    Endpoints are authoritative snapped pin positions (never ELK's floats);
    bend points snap to GRID; orthogonality repaired with L-jogs; zero-length
    segments dropped.
    """
    def _endpoint(section_pt, candidates):
        # nearest pin of this edge to ELK's float endpoint
        sx, sy = section_pt["x"], section_pt["y"]
        return min(candidates, key=lambda pid: (moved_pins[pid][0] - sx) ** 2
                                               + (moved_pins[pid][1] - sy) ** 2)

    segments = []
    for edge in elk_edges:
        pin_ids = list(edge["sources"]) + list(edge["targets"])
        for section in edge.get("sections", []):
            start_pid = _endpoint(section["startPoint"], pin_ids)
            end_pid = _endpoint(section["endPoint"], pin_ids)
            pts = [moved_pins[start_pid]]
            for bp in section.get("bendPoints", []):
                pts.append((_snap(bp["x"]), _snap(bp["y"])))
            pts.append(moved_pins[end_pid])
            pts = _orthogonalize(pts)
            for a, b in zip(pts, pts[1:]):
                if a != b:
                    segments.append((a, b))
    return segments


def find_junctions(segments):
    """Grid points where >=3 segment endpoints meet."""
    from collections import Counter
    counts = Counter()
    for a, b in segments:
        counts[a] += 1
        counts[b] += 1
    return sorted(pt for pt, n in counts.items() if n >= 3)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (9 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout grid snapping and orthogonal wire derivation"
```

---

### Task 8: Write-back + orchestrator (`elk_layout_schematic`)

**Files:**
- Modify: `kicad_skill/elk_layout.py`
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
import shutil
import tempfile

from kicad_skill.elk_layout import elk_layout_schematic


class TestElkLayoutSchematic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for fname in os.listdir(FIXTURE):
            shutil.copy(os.path.join(FIXTURE, fname), self.tmp)
        self.sch = os.path.join(self.tmp, "mcp_test.kicad_sch")
        self.table = os.path.join(self.tmp, "sym-lib-table")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @mock.patch("kicad_skill.elk_layout.run_elk")
    def test_identity_layout_preserves_connectivity(self, mock_elk):
        """ELK mocked to return every node at its CURRENT origin with no edge
        sections: symbols must not move; edge-nets (with no routes returned)
        fall back to labels; connectivity must survive exactly."""
        from kicad_skill.netlist_eval import extract_actual_netlist

        before = {frozenset(n) for n in
                  extract_actual_netlist(self.sch, self.table) if len(n) >= 2}

        def fake_run(graph):
            # echo each node at its current origin (bbox min corner), no routes
            _, symbols = load_fixture_symbols()
            by_ref = {s["ref"]: s for s in symbols}
            for c in graph["children"]:
                b = by_ref[c["id"]]["bbox"]
                c["x"], c["y"] = b.xmin, b.ymin
            for e in graph["edges"]:
                e["sections"] = []
            return graph
        mock_elk.side_effect = fake_run

        report = elk_layout_schematic(self.sch, self.table)
        self.assertTrue(report["ok"], report)

        after = {frozenset(n) for n in
                 extract_actual_netlist(self.sch, self.table) if len(n) >= 2}
        self.assertEqual(before, after)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestElkLayoutSchematic -v`
Expected: ImportError (`elk_layout_schematic` not defined).

- [ ] **Step 3: Implement** (append to `kicad_skill/elk_layout.py`)

```python
import uuid as _uuid

from .parser import parse_sexpr, format_sexpr
from .resolve_layout import _extract_symbols, _move_symbol
from .netlist_eval import extract_actual_netlist, compare
from .regenerate import _make_label, _label_orientation
from .schematic import load_sym_lib_table


def _make_wire(a, b):
    return ["wire",
            ["pts", ["xy", f"{a[0]:.3f}", f"{a[1]:.3f}"],
                    ["xy", f"{b[0]:.3f}", f"{b[1]:.3f}"]],
            ["stroke", ["width", "0"], ["type", "default"]],
            ["uuid", str(_uuid.uuid4())]]


def _make_junction(pt):
    return ["junction", ["at", f"{pt[0]:.3f}", f"{pt[1]:.3f}"],
            ["diameter", "0"], ["color", "0", "0", "0", "0"],
            ["uuid", str(_uuid.uuid4())]]


def elk_layout_schematic(sch_path, table_path=None, out_path=None,
                         fanout_threshold=4, dry_run=False):
    """Re-place and re-route one sheet via ELK. Returns a report dict.

    Gate: post-layout connectivity must equal pre-layout connectivity
    (zero shorts/opens) — report["ok"] False otherwise (file still written
    unless dry_run; caller decides severity).
    """
    if table_path is None:
        table_path = os.path.join(os.path.dirname(sch_path), "sym-lib-table")
    out_path = out_path or sch_path
    project_dir = os.path.dirname(sch_path)

    with open(sch_path, encoding="utf-8") as f:
        sch = parse_sexpr(f.read())

    local_defs = {}
    for child in sch[1:]:
        if isinstance(child, list) and child[0] == "lib_symbols":
            for sym in child[1:]:
                if isinstance(sym, list) and sym[0] == "symbol" and len(sym) > 1:
                    local_defs[sym[1]] = sym
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    symbols = _extract_symbols(sch, local_defs, lib_map, project_dir)

    # ground truth = pre-layout connectivity (named)
    raw_nets = [n for n in extract_actual_netlist(sch_path, table_path)]
    pin_positions = {}
    for s in symbols:
        for p in s["pins"]:
            pin_positions[f'{s["ref"]}:{p["number"]}'] = (p["x"], p["y"])
    labels_at = collect_labels_at(sch)
    named = name_nets([n for n in raw_nets if len(n) >= 2], pin_positions, labels_at)
    gt = [{"name": name, "pins": sorted(pins)} for name, pins in named]

    edge_nets, label_nets = classify_for_elk(named, fanout_threshold)

    graph = build_elk_graph(symbols, edge_nets)
    layouted = run_elk(graph)
    deltas = snap_deltas(layouted, symbols)

    if dry_run:
        return {"ok": True, "dry_run": True, "deltas": deltas,
                "edge_nets": [n for n, _ in edge_nets],
                "label_nets": [n for n, _ in label_nets]}

    # move symbols (live sexpr edit via _move_symbol)
    for sym in symbols:
        d = deltas.get(sym["ref"])
        if d:
            _move_symbol(sym, d[0], d[1])

    moved_pins = {}
    for s in symbols:
        for p in s["pins"]:
            moved_pins[f'{s["ref"]}:{p["number"]}'] = (p["x"], p["y"])

    # wires from ELK routes; edges that came back with no sections fall back
    # to labels (safe: labels always reconnect by name)
    routed, unrouted = [], []
    for edge in layouted.get("edges", []):
        (routed if edge.get("sections") else unrouted).append(edge)
    edge_by_id = {f"e{i}_{name}": (name, pins)
                  for i, (name, pins) in enumerate(edge_nets)}
    for edge in unrouted:
        if edge["id"] in edge_by_id:
            label_nets.append(edge_by_id[edge["id"]])
    segments = derive_wires(routed, moved_pins)
    junctions = find_junctions(segments)

    # strip ALL old wires, junctions, and old labels (full re-route)
    sch[:] = [c for c in sch if not (
        isinstance(c, list) and c and c[0] in ("wire", "junction", "label"))]

    centers = {s["ref"]: ((s["bbox"].xmin + s["bbox"].xmax) / 2,
                          (s["bbox"].ymin + s["bbox"].ymax) / 2) for s in symbols}
    for name, pins in label_nets:
        for pid in sorted(pins):
            x, y = moved_pins[pid]
            cx, cy = centers.get(pid.split(":")[0], (x, y))
            angle, justify = _label_orientation(x, y, cx, cy)
            sch.append(_make_label(name, x, y, angle, justify))
    for a, b in segments:
        sch.append(_make_wire(a, b))
    for pt in junctions:
        sch.append(_make_junction(pt))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(sch))

    rep = compare(extract_actual_netlist(out_path, table_path), gt)
    return {"ok": not rep["fatal"], "report": rep, "deltas": deltas,
            "wires": len(segments), "labels": sum(len(p) for _, p in label_nets),
            "junctions": len(junctions)}
```

Implementer notes:
- `global_label` elements are intentionally NOT stripped (cross-sheet connectivity); only `label` (local), `wire`, `junction` are rebuilt.
- Check `parser.py` for the exact names `parse_sexpr` / `format_sexpr` (used everywhere else, e.g. `regenerate.py` — copy its import line).
- `compare(actual, gt)` is `netlist_eval.compare`; gt entries need `{"name", "pins"}` keys — built above.
- If the identity test exposes ordering/format issues (e.g. `_make_wire` stroke shape differing from what `evaluate_layout` expects), copy the exact wire/junction sexpr shape from `regenerate.py`/`schematic.py`'s existing emitters instead of the literals above.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (10 tests)
Run: `uv run python -m unittest discover -s tests -v` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/elk_layout.py tests/test_elk_layout.py
git commit -m "feat: elk-layout write-back and orchestrator with connectivity gate"
```

---

### Task 9: CLI wiring (`elk-layout` subcommand)

**Files:**
- Modify: `kicad_skill/main.py`

- [ ] **Step 1: Add subparser** (after the `render-netlist` parser block, before `args = parser.parse_args()`)

```python
    # elk-layout parser
    elk_parser = subparsers.add_parser("elk-layout", help="Re-place and re-route a schematic via ELK (elkjs) auto-layout")
    elk_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    elk_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")
    elk_parser.add_argument("--output", help="Output schematic path (default: overwrite input)")
    elk_parser.add_argument("--fanout-threshold", type=int, default=4, help="Nets with >= this many pins become labels instead of routed wires (default: 4)")
    elk_parser.add_argument("--dry-run", action="store_true", help="Print layout plan without writing")
```

- [ ] **Step 2: Add dispatch branch** (before trailing `else:`)

```python
    elif args.command == 'elk-layout':
        from .elk_layout import elk_layout_schematic
        rep = elk_layout_schematic(
            args.schematic, args.table, out_path=args.output,
            fanout_threshold=args.fanout_threshold, dry_run=args.dry_run)
        if rep.get("dry_run"):
            print(f"Deltas: {len(rep['deltas'])} symbols")
            print(f"Wire nets:  {', '.join(rep['edge_nets']) or '(none)'}")
            print(f"Label nets: {', '.join(rep['label_nets']) or '(none)'}")
        else:
            print(f"Moved {len(rep['deltas'])} symbols, {rep['wires']} wire segments, "
                  f"{rep['labels']} labels, {rep['junctions']} junctions")
            if rep["ok"]:
                print("Connectivity check: OK")
            else:
                print(f"WARNING: connectivity changed! {rep['report']}")
                sys.exit(1)
```

- [ ] **Step 3: Verify wiring**

Run: `uv run python -m kicad_skill.main elk-layout --help`
Expected: usage with all 5 flags, exit 0.
Run: `uv run python -m unittest discover -s tests -v` → no regressions.

- [ ] **Step 4: Commit**

```bash
git add kicad_skill/main.py
git commit -m "feat: add elk-layout subcommand"
```

---

### Task 10: `regenerate --routing elk`

**Files:**
- Modify: `kicad_skill/regenerate.py` (in `regenerate_schematic`)
- Modify: `kicad_skill/main.py` (the `regenerate`/relevant subparser's `--routing` choices — find it with `grep -n "routing" kicad_skill/main.py`; if `--routing` has a `choices=` list, add `"elk"`)
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
class TestRegenerateElkMode(unittest.TestCase):
    @mock.patch("kicad_skill.elk_layout.run_elk")
    def test_regenerate_with_elk_routing(self, mock_elk):
        from kicad_skill.regenerate import regenerate_schematic

        def echo_origin(graph):
            for c in graph["children"]:
                c.setdefault("x", 0.0)
                c.setdefault("y", 0.0)
            for e in graph["edges"]:
                e["sections"] = []
            return graph
        mock_elk.side_effect = echo_origin

        tmp = tempfile.mkdtemp()
        try:
            for fname in os.listdir(FIXTURE):
                shutil.copy(os.path.join(FIXTURE, fname), tmp)
            out = os.path.join(tmp, "regen_elk.kicad_sch")
            gt = os.path.join(tmp, "can_node.groundtruth.json")
            table = os.path.join(tmp, "sym-lib-table")
            out_sch, rep = regenerate_schematic(gt, table, out,
                                                use_erc=False, routing="elk")
            self.assertFalse(rep["fatal"], rep)
        finally:
            shutil.rmtree(tmp)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_elk_layout.TestRegenerateElkMode -v`
Expected: fails — `routing="elk"` unknown / behaves as default without ELK delegation. (Inspect `regenerate_schematic` + `_build_once` first: if unknown routing strings currently fall through silently, the test may even pass by accident — in that case assert `mock_elk.called` too, which pins the delegation.)

Add to the test (required regardless):
```python
            self.assertTrue(mock_elk.called)
```

- [ ] **Step 3: Implement**

In `regenerate_schematic` (`kicad_skill/regenerate.py`), at the top of the function body add:

```python
    if routing == "elk":
        # Build an all-labels schematic (electrically clean by construction),
        # then hand placement+routing to the ELK engine.
        out_sch, rep = regenerate_schematic(gt_path, table_path, out_sch,
                                            max_iter=max_iter, use_erc=use_erc,
                                            routing="labels")
        from .elk_layout import elk_layout_schematic
        elk_rep = elk_layout_schematic(out_sch, table_path)
        if not elk_rep["ok"]:
            raise RuntimeError(f"elk-layout broke connectivity: {elk_rep['report']}")
        rep["elk"] = {k: elk_rep[k] for k in ("wires", "labels", "junctions")}
        return out_sch, rep
```

Check first (`grep -n '"labels"' kicad_skill/regenerate.py` / read `_build_once`'s routing param) that `routing="labels"` is an existing accepted value producing an all-labels build; if the existing all-labels mode is spelled differently (e.g. only reachable via `forced_labels`), use the actual mechanism and note the deviation in the commit message.

In `kicad_skill/main.py`, extend the regenerate subparser's `--routing` argument to accept `elk` (locate with `grep -n "routing" kicad_skill/main.py`; add to `choices` list if present).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m unittest tests.test_elk_layout -v` → OK (11 tests)
Run: `uv run python -m unittest discover -s tests -v` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/regenerate.py kicad_skill/main.py tests/test_elk_layout.py
git commit -m "feat: regenerate --routing elk delegates layout to ELK engine"
```

---

### Task 11: Gated integration test (real elkjs) + verification gate

**Files:**
- Test: `tests/test_elk_layout.py` (append)

- [ ] **Step 1: Write the integration test** (append)

```python
import shutil as _shutil

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
_HAS_ELKJS = (_shutil.which("node") is not None
              and os.path.isdir(os.path.join(_TOOLS, "node_modules", "elkjs")))


@unittest.skipUnless(_HAS_ELKJS, "node + tools/node_modules/elkjs required (npm install --prefix tools/)")
class TestElkIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for fname in os.listdir(FIXTURE):
            shutil.copy(os.path.join(FIXTURE, fname), self.tmp)
        self.sch = os.path.join(self.tmp, "mcp_test.kicad_sch")
        self.table = os.path.join(self.tmp, "sym-lib-table")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_full_gate_on_can_node(self):
        from kicad_skill.elk_layout import elk_layout_schematic
        from kicad_skill.erc import find_kicad_cli, run_erc
        from kicad_skill.evaluate_layout import evaluate_schematic_layout

        # baseline score of the fixture as-is (current-pipeline output)
        baseline = evaluate_schematic_layout(self.sch, self.table)

        rep = elk_layout_schematic(self.sch, self.table)

        # Gate 1: connectivity preserved
        self.assertTrue(rep["ok"], rep)

        # Gate 2: ERC clean (when kicad-cli present)
        if find_kicad_cli():
            erc = run_erc(self.sch)
            self.assertTrue(erc["ok"], erc["violations"])

        # Gate 3: quality — not worse than the input layout
        after = evaluate_schematic_layout(self.sch, self.table)
        self.assertFalse(after["fatal"], after)
        self.assertGreaterEqual(after["score"], baseline["score"],
                                f"ELK {after['score']} < baseline {baseline['score']}")
```

Note: the can_node fixture is a deliberately-shorted design (VDD/GND merged) — `rep["ok"]` gates on connectivity being PRESERVED (same nets before/after), not on the design being correct; the pre-layout extract is the ground truth. If Gate 2 ERC fails on the fixture's pre-existing electrical problems (shorts existed before layout), restrict Gate 2 to layout-class violations (`wire_dangling`, `label_dangling`, `pin_not_connected`) rather than all errors, and document that in the test with a comment.

- [ ] **Step 2: Run it for real**

```bash
npm install --prefix tools/   # if not done in Task 1
uv run python -m unittest tests.test_elk_layout.TestElkIntegration -v
```
Expected: PASS (or SKIP with the named reason if node/elkjs absent). If it fails: this is the moment layout-quality tuning happens — adjust `spacing.*` values in `build_elk_graph`, re-run. Do not weaken the gates.

- [ ] **Step 3: Full suite**

Run: `uv run python -m unittest discover -s tests -v` → all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_elk_layout.py
git commit -m "test: elk-layout integration gate (connectivity + ERC + quality score)"
```

---

### Task 12: SKILL.md documentation

**Files:**
- Modify: `skills/kicad-helper/SKILL.md`

- [ ] **Step 1: Add numbered entry** (after the current last `###` section — check numbering with `grep -n '^### ' skills/kicad-helper/SKILL.md`; expected next number 9)

```markdown
### 9. ELK Auto-Layout (`elk-layout`)
Re-places and re-routes an entire schematic sheet using the ELK layered algorithm (via elkjs): globally-optimized component placement plus orthogonal wire routing, with pins fixed in place (`FIXED_POS`). Power nets and high-fanout nets become net labels; 2-3 pin signal nets are routed as wires. Replaces the manual `place` + `connect` + `resolve` flow for whole-sheet layout.

One-time setup (needs network): `npm install --prefix tools/` (from the kicad-helper repo root).

```bash
/Users/ktchou/kicad-helper/kicad-helper elk-layout \
  --schematic "path/to/schematic.kicad_sch" \
  --output "path/to/layouted.kicad_sch"
```
* **Arguments:**
  - `--schematic`: Path to the `.kicad_sch` file.
  - `--table`: Path to `sym-lib-table` (default: same folder as schematic).
  - `--output`: Output path (default: overwrite input).
  - `--fanout-threshold`: Nets with >= this many pins become labels (default: 4).
  - `--dry-run`: Print the layout plan without writing.
* **Also:** `regenerate --routing elk` regenerates from a ground-truth netlist and lays out via ELK.
* **Scope limits:** one sheet per run (no hierarchical multi-sheet layout); symbols keep their input rotation; no symmetry/grouping constraints yet; existing local labels/wires/junctions on the sheet are rebuilt (global labels preserved); requires `node` + one-time `npm install --prefix tools/`.
```

(As with the render-netlist entry: don't include the outer wrapping fence — write heading/prose + bash block + bullets matching sections 1-8's style.)

- [ ] **Step 2: Read back**

Run: `grep -n "elk-layout" skills/kicad-helper/SKILL.md`
Expected: heading + command line present.

- [ ] **Step 3: Commit**

```bash
git add skills/kicad-helper/SKILL.md
git commit -m "docs: document elk-layout subcommand"
```

---

### Task 13: Manual end-to-end verification

No commit — verification only. Needs network (npm) + node.

- [ ] **Step 1: Real run on can_node**

```bash
npm install --prefix tools/
cp -r tests/fixtures/can_node /tmp/elk_e2e
uv run python -m kicad_skill.main elk-layout \
  --schematic /tmp/elk_e2e/mcp_test.kicad_sch \
  --table /tmp/elk_e2e/sym-lib-table
```
Expected: `Moved N symbols, ...` + `Connectivity check: OK`, exit 0.

- [ ] **Step 2: Score + eyeball**

```bash
uv run python kicad_skill/evaluate_layout.py /tmp/elk_e2e/mcp_test.kicad_sch /tmp/elk_e2e/sym-lib-table
uv run python -m kicad_skill.main snapshot --schematic /tmp/elk_e2e/mcp_test.kicad_sch --output /tmp/elk_e2e/after.svg
open /tmp/elk_e2e/after.svg
```
Expected: score >= baseline, no FATAL; SVG visually sane (no overlaps, left-to-right flow, wires orthogonal, labels readable).

- [ ] **Step 3: Full suite once more**

Run: `uv run python -m unittest discover -s tests -v` → all pass, integration test ran (not skipped).
