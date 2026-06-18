# Wire Complexity & Label Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure per-connection wire routing complexity and auto-convert the worst pin-to-pin connections into local-label connections until the schematic's total complexity drops below a threshold, never altering the netlist.

**Architecture:** A self-contained `kicad_skill/wire_complexity.py` reuses the existing union-find net model and pin extraction. It scores each two-terminal (pin-to-pin) wire chain by crossings (with different nets), bends, and length; an iterative simplifier deletes the highest-scoring chain and tags both pins with a matching local label, guarded by a net-preservation gate that rolls back any change to pin connectivity. A CLI subcommand and a read-only `evaluate_layout` metric expose it.

**Tech Stack:** Python 3.10+, `unittest`, KiCad S-expression AST (`kicad_skill.parser`), `uv` for running.

**Spec:** `docs/superpowers/specs/2026-06-16-wire-complexity-design.md`

---

## File Structure

- Create: `kicad_skill/wire_complexity.py` — net model, connection reconstruction, scoring, conversion, simplifier.
- Create: `tests/test_wire_complexity.py` — unit tests.
- Modify: `kicad_skill/main.py` — add `simplify-wires` subcommand.
- Modify: `kicad_skill/evaluate_layout.py` — add read-only `wire_complexity_total` metric.

Conventions to follow (match existing code):
- Grid = `1.27` mm; grid keys are `(int(round(x/1.27)), int(round(y/1.27)))` — identical to `module.grid_key`.
- Wires are emitted one segment per `wire` S-expr (see `schematic.make_wire_sexpr`).
- Symbol pins extracted via `module.get_symbol_pins_global(instance, defn)`.
- Errors raise `ValueError` (like `place_symbols_and_resolve`).

---

## Task 1: Module scaffold + parsing helpers

**Files:**
- Create: `kicad_skill/wire_complexity.py`
- Test: `tests/test_wire_complexity.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import tempfile
import unittest
from kicad_skill.wire_complexity import _parse_schematic, _collect_wires, _collect_labels

SCH = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (wire (pts (xy 100.33 100.33) (xy 110.49 100.33)) (stroke (width 0) (type default)) (uuid "w1"))
  (label "SIG" (at 110.49 100.33 0) (effects (font (size 1.27 1.27))) (uuid "l1"))
)
"""

class TestParsing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        with open(self.sch, "w") as f:
            f.write(SCH)

    def tearDown(self):
        self.td.cleanup()

    def test_collect_wires_and_labels(self):
        sx = _parse_schematic(self.sch)
        wires = _collect_wires(sx)
        self.assertEqual(len(wires), 1)
        node, ga, gb = wires[0]
        self.assertEqual(ga, (79, 79))   # 100.33/1.27 = 79
        self.assertEqual(gb, (87, 79))   # 110.49/1.27 = 87
        labels = _collect_labels(sx)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0][1], "SIG")
        self.assertEqual(labels[0][2], (87, 79))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_wire_complexity -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name '_parse_schematic'`.

- [ ] **Step 3: Write minimal implementation**

```python
# kicad_skill/wire_complexity.py
import os
from .parser import parse_sexpr, format_sexpr
from .module import grid_key, get_symbol_pins_global
from .schematic import load_sym_lib_table, find_symbol_definition, make_wire_sexpr

GRID = 1.27


def _parse_schematic(sch_path):
    if not os.path.exists(sch_path):
        raise ValueError(f"Schematic file {sch_path} not found")
    with open(sch_path, "r", encoding="utf-8") as f:
        sx = parse_sexpr(f.read())
    if not sx or sx[0] != "kicad_sch":
        raise ValueError(f"Invalid KiCad schematic file {sch_path}")
    return sx


def _seg_endpoints(wire_node):
    pts = next((s for s in wire_node[1:] if isinstance(s, list) and s[0] == "pts"), None)
    if not pts:
        return None
    cs = [(float(a[1]), float(a[2])) for a in pts[1:]
          if isinstance(a, list) and len(a) > 2 and a[0] == "xy"]
    if len(cs) < 2:
        return None
    return grid_key(*cs[0]), grid_key(*cs[-1])


def _collect_wires(sx):
    """Return [(node, gk_a, gk_b)] for each wire segment."""
    out = []
    for ch in sx[1:]:
        if isinstance(ch, list) and ch and ch[0] == "wire":
            ep = _seg_endpoints(ch)
            if ep:
                out.append((ch, ep[0], ep[1]))
    return out


def _collect_labels(sx):
    """Return [(node, text, gk)] for each local label."""
    out = []
    for ch in sx[1:]:
        if isinstance(ch, list) and ch and ch[0] == "label" and len(ch) > 1:
            at = next((s for s in ch[1:] if isinstance(s, list) and s[0] == "at"), None)
            if at:
                out.append((ch, ch[1], grid_key(float(at[1]), float(at[2]))))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_wire_complexity -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/wire_complexity.py tests/test_wire_complexity.py
git commit -m "feat(wire-complexity): scaffold module with wire/label parsing"
```

---

## Task 2: Pin collection + net model + connection reconstruction

**Files:**
- Modify: `kicad_skill/wire_complexity.py`
- Test: `tests/test_wire_complexity.py`

Connection model (per spec): a connection is a simple wire chain between two **terminal
pins** whose interior nodes are pure pass-throughs (degree exactly 2, not a pin). Chains
that hit a branch node (degree != 2) or dead-end are intentionally NOT treated as
pin-to-pin connections — those are multi-point net internals (VDD/GND) we leave alone (and
which would fail the gate in Task 4 anyway).

- [ ] **Step 1: Write the failing test**

```python
from kicad_skill.wire_complexity import _collect_pins, _build_net_find, _reconstruct_connections

# Two ICs joined by an L-shaped 2-segment wire = one pin-to-pin connection.
SCH2 = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (lib_symbols
    (symbol "lib:IC"
      (property "Reference" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "IC_1_1"
        (pin passive line (at -2.54 0 0) (length 2.54)
          (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )
  (symbol (lib_id "lib:IC") (at 50.8 50.8 0)
    (property "Reference" "U1" (at 50.8 45.72 0)) (property "Value" "IC" (at 50.8 55.88 0)))
  (symbol (lib_id "lib:IC") (at 76.2 60.96 0)
    (property "Reference" "U2" (at 76.2 55.88 0)) (property "Value" "IC" (at 76.2 66.04 0)))
  (wire (pts (xy 48.26 50.8) (xy 48.26 60.96)) (stroke (width 0) (type default)) (uuid "w1"))
  (wire (pts (xy 48.26 60.96) (xy 73.66 60.96)) (stroke (width 0) (type default)) (uuid "w2"))
)
"""

class TestConnections(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        self.table = os.path.join(self.td.name, "sym-lib-table")
        with open(self.table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(self.sch, "w") as f:
            f.write(SCH2)

    def tearDown(self):
        self.td.cleanup()

    def test_reconstruct_single_connection(self):
        sx = _parse_schematic(self.sch)
        pins = _collect_pins(sx, self.table, self.td.name)
        # U1.A pin tip at (48.26,50.8); U2.A pin tip at (73.66,60.96)
        self.assertEqual(len(pins), 2)
        conns = _reconstruct_connections(sx, pins)
        self.assertEqual(len(conns), 1)
        c = conns[0]
        refs = {f"{c['pin_a']['ref']}:{c['pin_a']['name']}",
                f"{c['pin_b']['ref']}:{c['pin_b']['name']}"}
        self.assertEqual(refs, {"U1:A", "U2:A"})
        self.assertEqual(len(c["wire_nodes"]), 2)
        # ordered path U1 -> bend -> U2
        self.assertEqual(c["path"][0], (38, 40))   # 48.26/1.27=38, 50.8/1.27=40
        self.assertEqual(c["path"][-1], (58, 48))  # 73.66/1.27=58, 60.96/1.27=48

    def test_net_find_groups_endpoints(self):
        sx = _parse_schematic(self.sch)
        find = _build_net_find(sx)
        self.assertEqual(find((38, 40)), find((58, 48)))  # same net via wires
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_wire_complexity.TestConnections -v`
Expected: FAIL with `ImportError` for `_collect_pins`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to kicad_skill/wire_complexity.py

def _collect_pins(sx, table_path, project_dir):
    """Return [{'ref','name','number','gk','x','y'}] for every symbol-instance pin."""
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    local_defs = {}
    for ch in sx[1:]:
        if isinstance(ch, list) and ch and ch[0] == "lib_symbols":
            for s in ch[1:]:
                if isinstance(s, list) and s[0] == "symbol" and len(s) > 1:
                    local_defs[s[1]] = s
    pins = []
    for ch in sx[1:]:
        if not (isinstance(ch, list) and ch and ch[0] == "symbol"):
            continue
        lib_id = ref = None
        for s in ch[1:]:
            if isinstance(s, list) and len(s) > 1:
                if s[0] == "lib_id":
                    lib_id = s[1]
                elif s[0] == "property" and len(s) > 2 and s[1] == "Reference":
                    ref = s[2]
        if not ref:
            continue
        defn = local_defs.get(lib_id)
        if not defn and lib_id and ":" in lib_id:
            ln, sn = lib_id.split(":", 1)
            defn = find_symbol_definition(ln, sn, lib_map, project_dir)
        for p in get_symbol_pins_global(ch, defn):
            pins.append({"ref": ref, "name": p["name"], "number": p["number"],
                         "x": p["x"], "y": p["y"], "gk": grid_key(p["x"], p["y"])})
    return pins


def _build_net_find(sx, pins=None):
    """Union-find over explicit connections: wire endpoints, plus same-text labels."""
    uf = {}

    def find(n):
        uf.setdefault(n, n)
        while uf[n] != n:
            uf[n] = uf[uf[n]]
            n = uf[n]
        return n

    def union(a, b):
        uf[find(a)] = find(b)

    for _, ga, gb in _collect_wires(sx):
        union(ga, gb)
    # same-text labels are electrically one net
    by_text = {}
    for _, text, gk in _collect_labels(sx):
        by_text.setdefault(text, []).append(gk)
    for coords in by_text.values():
        for c in coords[1:]:
            union(coords[0], c)
    return find


def _adjacency(wires):
    """gk -> list of (neighbor_gk, wire_node). Also degree map."""
    adj = {}
    for node, ga, gb in wires:
        adj.setdefault(ga, []).append((gb, node))
        adj.setdefault(gb, []).append((ga, node))
    return adj


def _reconstruct_connections(sx, pins):
    """Pin-to-pin connections: simple chains whose interior nodes are degree-2 non-pins."""
    wires = _collect_wires(sx)
    adj = _adjacency(wires)
    pin_gks = {}
    for p in pins:
        pin_gks.setdefault(p["gk"], p)

    conns = []
    seen = set()  # frozenset of wire-node ids, to dedup
    for start_gk, start_pin in pin_gks.items():
        for nbr, w0 in adj.get(start_gk, []):
            # Walk the degree-2 chain until another pin (success) or a branch (abort).
            path = [start_gk, nbr]
            wire_nodes = [w0]
            prev, curr = start_gk, nbr
            ok = False
            while True:
                if curr in pin_gks and curr != start_gk:
                    ok = True
                    break
                neighbors = adj.get(curr, [])
                if len(neighbors) != 2:
                    break  # branch or dead-end: not a clean pin-to-pin connection
                nxt = next(((g, n) for (g, n) in neighbors if g != prev), None)
                if nxt is None:
                    break
                path.append(nxt[0])
                wire_nodes.append(nxt[1])
                prev, curr = curr, nxt[0]
            if not ok:
                continue
            key = frozenset(id(n) for n in wire_nodes)
            if key in seen:
                continue
            seen.add(key)
            conns.append({
                "pin_a": start_pin,
                "pin_b": pin_gks[curr],
                "path": path,
                "wire_nodes": wire_nodes,
            })
    return conns
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_wire_complexity.TestConnections -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/wire_complexity.py tests/test_wire_complexity.py
git commit -m "feat(wire-complexity): pin collection, net model, connection reconstruction"
```

---

## Task 3: Complexity scoring + `score_wire_complexity`

**Files:**
- Modify: `kicad_skill/wire_complexity.py`
- Test: `tests/test_wire_complexity.py`

- [ ] **Step 1: Write the failing test**

```python
from kicad_skill.wire_complexity import (
    _path_bends, _path_length, _count_crossings, score_wire_complexity, DEFAULT_WEIGHTS,
)

class TestScoring(unittest.TestCase):
    def test_bends_and_length(self):
        straight = [(0, 0), (5, 0)]
        self.assertEqual(_path_bends(straight), 0)
        self.assertEqual(_path_length(straight), 5)
        ell = [(0, 0), (0, 3), (4, 3)]
        self.assertEqual(_path_bends(ell), 1)
        self.assertEqual(_path_length(ell), 7)

    def test_crossing_perpendicular_different_net(self):
        # net X: horizontal y=0 from x=0..10 ; net Y: vertical x=5 from y=-5..5
        segs_self = [((0, 0), (10, 0))]
        other_segs = [((5, -5), (5, 5))]  # different net
        self.assertEqual(_count_crossings(segs_self, other_segs), 1)

    def test_no_crossing_when_shared_endpoint(self):
        segs_self = [((0, 0), (10, 0))]
        other_segs = [((10, 0), (10, 5))]  # touches at endpoint, not a crossing
        self.assertEqual(_count_crossings(segs_self, other_segs), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_wire_complexity.TestScoring -v`
Expected: FAIL with `ImportError` for `_path_bends`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to kicad_skill/wire_complexity.py

DEFAULT_WEIGHTS = {"crossings": 10.0, "bends": 2.0, "length": 0.5}


def _direction(a, b):
    return (0 if b[0] == a[0] else (1 if b[0] > a[0] else -1),
            0 if b[1] == a[1] else (1 if b[1] > a[1] else -1))


def _path_bends(path):
    bends = 0
    for i in range(1, len(path) - 1):
        if _direction(path[i - 1], path[i]) != _direction(path[i], path[i + 1]):
            bends += 1
    return bends


def _path_length(path):
    return sum(abs(path[i + 1][0] - path[i][0]) + abs(path[i + 1][1] - path[i][1])
               for i in range(len(path) - 1))


def _segments_of_path(path):
    return [(path[i], path[i + 1]) for i in range(len(path) - 1)]


def _cross_point(s1, s2):
    """Return the integer-grid crossing point of one H and one V segment, else None.
    Excludes crossings that fall on an endpoint of either segment (a shared endpoint
    is a join, not a crossing)."""
    (a1, a2), (b1, b2) = s1, s2

    def is_h(s):
        return s[0][1] == s[1][1]

    def is_v(s):
        return s[0][0] == s[1][0]

    if is_h(s1) and is_v(s2):
        h, v = s1, s2
    elif is_v(s1) and is_h(s2):
        v, h = s1, s2
    else:
        return None
    hy = h[0][1]
    vx = v[0][0]
    hx_lo, hx_hi = sorted((h[0][0], h[1][0]))
    vy_lo, vy_hi = sorted((v[0][1], v[1][1]))
    if hx_lo <= vx <= hx_hi and vy_lo <= hy <= vy_hi:
        pt = (vx, hy)
        # exclude shared endpoints
        if pt in (h[0], h[1], v[0], v[1]):
            return None
        return pt
    return None


def _count_crossings(self_segs, other_segs):
    n = 0
    for s1 in self_segs:
        for s2 in other_segs:
            if _cross_point(s1, s2) is not None:
                n += 1
    return n


def score_wire_complexity(sch_path, table_path, weights=None):
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    sx = _parse_schematic(sch_path)
    project_dir = os.path.dirname(os.path.abspath(sch_path))
    pins = _collect_pins(sx, table_path, project_dir)
    find = _build_net_find(sx, pins)
    conns = _reconstruct_connections(sx, pins)

    # Segment -> net root, for crossing classification.
    wires = _collect_wires(sx)
    seg_net = {}
    for _, ga, gb in wires:
        seg_net[(ga, gb)] = find(ga)

    results = []
    total = 0.0
    for c in conns:
        self_segs = _segments_of_path(c["path"])
        self_root = find(c["path"][0])
        other = [(ga, gb) for (_, ga, gb) in wires if find(ga) != self_root]
        crossings = _count_crossings(self_segs, other)
        bends = _path_bends(c["path"])
        length = _path_length(c["path"])
        score = w["crossings"] * crossings + w["bends"] * bends + w["length"] * length
        total += score
        results.append({
            "pin_a": f"{c['pin_a']['ref']}:{c['pin_a']['name']}",
            "pin_b": f"{c['pin_b']['ref']}:{c['pin_b']['name']}",
            "score": score, "crossings": crossings, "bends": bends, "length": length,
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"total": total, "connections": results}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_wire_complexity.TestScoring -v`
Expected: PASS.

- [ ] **Step 5: Add a monotonicity + end-to-end score test**

```python
class TestScoreMonotonic(unittest.TestCase):
    def test_more_bends_scores_higher(self):
        straight = [(0, 0), (10, 0)]
        zig = [(0, 0), (0, 2), (5, 2), (5, 0), (10, 0)]
        from kicad_skill.wire_complexity import _path_bends, _path_length, DEFAULT_WEIGHTS
        w = DEFAULT_WEIGHTS
        s_straight = w["bends"] * _path_bends(straight) + w["length"] * _path_length(straight)
        s_zig = w["bends"] * _path_bends(zig) + w["length"] * _path_length(zig)
        self.assertGreater(s_zig, s_straight)
```

- [ ] **Step 6: Run all wire-complexity tests**

Run: `uv run python -m unittest tests.test_wire_complexity -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kicad_skill/wire_complexity.py tests/test_wire_complexity.py
git commit -m "feat(wire-complexity): scoring (crossings, bends, length) + score_wire_complexity"
```

---

## Task 4: Conversion + net-preservation gate + `simplify_wires`

**Files:**
- Modify: `kicad_skill/wire_complexity.py`
- Test: `tests/test_wire_complexity.py`

- [ ] **Step 1: Write the failing test**

```python
from kicad_skill.wire_complexity import simplify_wires, score_wire_complexity
from kicad_skill.evaluate_layout import evaluate_schematic_layout

class TestSimplify(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sch = os.path.join(self.td.name, "t.kicad_sch")
        self.table = os.path.join(self.td.name, "sym-lib-table")
        with open(self.table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(self.sch, "w") as f:
            f.write(SCH2)  # the two-IC single-connection schematic from Task 2

    def tearDown(self):
        self.td.cleanup()

    def test_convert_lowers_total_and_preserves_net(self):
        before = score_wire_complexity(self.sch, self.table)["total"]
        res = simplify_wires(self.sch, self.table, threshold=0.0)  # force conversion
        self.assertEqual(len(res["converted"]), 1)
        self.assertLess(res["total_after"], before)
        # net preserved: U1.A and U2.A still same net (now via matching labels)
        ev = evaluate_schematic_layout(self.sch, self.table)
        self.assertEqual(ev["shorts"], 0)
        self.assertEqual(ev["dangling"], 0)
        # both endpoints now carry a label of equal text
        from kicad_skill.wire_complexity import _parse_schematic, _collect_labels
        labels = _collect_labels(_parse_schematic(self.sch))
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0][1], labels[1][1])

    def test_dry_run_does_not_write(self):
        with open(self.sch) as f:
            original = f.read()
        res = simplify_wires(self.sch, self.table, threshold=0.0, dry_run=True)
        with open(self.sch) as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(len(res["converted"]), 1)  # plan still reported
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_wire_complexity.TestSimplify -v`
Expected: FAIL with `ImportError` for `simplify_wires`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to kicad_skill/wire_complexity.py
import uuid as _uuid


def _pin_partition(sx, pins, table_path, project_dir):
    """Frozenset of frozensets: which pins are mutually connected (net equivalence)."""
    find = _build_net_find(sx, pins)
    groups = {}
    for p in pins:
        root = find(p["gk"])
        groups.setdefault(root, set()).add(f"{p['ref']}:{p['number']}")
    return frozenset(frozenset(g) for g in groups.values())


def _net_name_for(conn, sx, find, counter):
    """existing label on net -> pin-derived -> NET_n."""
    self_root = find(conn["path"][0])
    for _, text, gk in _collect_labels(sx):
        if find(gk) == self_root:
            return text
    p = conn["pin_a"]
    base = (p["name"] or p["number"]).replace("/", "_").replace(" ", "_")
    return f"{p['ref']}_{base}" if base else f"NET_{counter}"


def _label_sexpr(text, gk, toward_left):
    x, y = gk[0] * GRID, gk[1] * GRID
    angle = "180" if toward_left else "0"
    justify = "right" if toward_left else "left"
    return ["label", text,
            ["at", f"{x:.3f}", f"{y:.3f}", angle],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", justify]],
            ["uuid", str(_uuid.uuid4())]]


def _apply_conversion(sx, conn, net_name):
    """Delete the connection's wires; add a stub+label at each terminal pin.
    Mutates sx in place. Returns nothing."""
    remove = set(id(n) for n in conn["wire_nodes"])
    sx[:] = [sx[0]] + [c for c in sx[1:]
                       if not (isinstance(c, list) and c and c[0] == "wire" and id(c) in remove)]
    for pin in (conn["pin_a"], conn["pin_b"]):
        gk = pin["gk"]
        # stub one grid step toward open space (left for pin_a, right for pin_b is naive;
        # use sign from the connection's first/last direction away from the pin body).
        toward_left = pin is conn["pin_a"]
        stub_gk = (gk[0] - 1, gk[1]) if toward_left else (gk[0] + 1, gk[1])
        sx.append(make_wire_sexpr(gk[0] * GRID, gk[1] * GRID,
                                  stub_gk[0] * GRID, stub_gk[1] * GRID))
        sx.append(_label_sexpr(net_name, stub_gk, toward_left))


def simplify_wires(sch_path, table_path, threshold=50.0, weights=None,
                   max_conversions=None, dry_run=False):
    project_dir = os.path.dirname(os.path.abspath(sch_path))
    sx = _parse_schematic(sch_path)
    pins = _collect_pins(sx, table_path, project_dir)
    baseline_partition = _pin_partition(sx, pins, table_path, project_dir)

    total_before = score_wire_complexity(sch_path, table_path, weights)["total"]
    converted, skipped = [], []
    counter = 0
    tried = set()

    def current_total_and_conns():
        sc = _score_from_ast(sx, pins, weights)
        return sc["total"], sc["connections_full"]

    total, conns = current_total_and_conns()
    while total > threshold:
        if max_conversions is not None and len(converted) >= max_conversions:
            break
        cand = next((c for c in conns
                     if frozenset(id(n) for n in c["_conn"]["wire_nodes"]) not in tried), None)
        if cand is None:
            break
        conn = cand["_conn"]
        tried.add(frozenset(id(n) for n in conn["wire_nodes"]))
        # snapshot for rollback
        snapshot = format_sexpr(sx)
        find = _build_net_find(sx, pins)
        counter += 1
        name = _net_name_for(conn, sx, find, counter)
        _apply_conversion(sx, conn, name)
        new_partition = _pin_partition(sx, pins, table_path, project_dir)
        if new_partition == baseline_partition:
            converted.append({"net_name": name, "pin_a": cand["pin_a"],
                              "pin_b": cand["pin_b"], "score": cand["score"]})
            tried = set()  # geometry changed; re-evaluate everything
            total, conns = current_total_and_conns()
        else:
            sx[:] = parse_sexpr(snapshot)  # rollback
            skipped.append({"pin_a": cand["pin_a"], "pin_b": cand["pin_b"],
                            "reason": "would change connectivity"})

    if not dry_run and converted:
        with open(sch_path, "w", encoding="utf-8") as f:
            f.write(format_sexpr(sx))
    total_after = _score_from_ast(sx, pins, weights)["total"]
    return {"total_before": total_before, "total_after": total_after,
            "converted": converted, "skipped_unsafe": skipped}
```

- [ ] **Step 4: Add the shared `_score_from_ast` helper used above**

Refactor scoring so the loop can score an in-memory AST (avoid re-reading the file each
iteration). Add this helper and make `score_wire_complexity` delegate to it.

```python
# append to kicad_skill/wire_complexity.py

def _score_from_ast(sx, pins, weights=None):
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    find = _build_net_find(sx, pins)
    conns = _reconstruct_connections(sx, pins)
    wires = _collect_wires(sx)
    results, full, total = [], [], 0.0
    for c in conns:
        self_segs = _segments_of_path(c["path"])
        self_root = find(c["path"][0])
        other = [(ga, gb) for (_, ga, gb) in wires if find(ga) != self_root]
        crossings = _count_crossings(self_segs, other)
        bends = _path_bends(c["path"])
        length = _path_length(c["path"])
        score = w["crossings"] * crossings + w["bends"] * bends + w["length"] * length
        total += score
        row = {"pin_a": f"{c['pin_a']['ref']}:{c['pin_a']['name']}",
               "pin_b": f"{c['pin_b']['ref']}:{c['pin_b']['name']}",
               "score": score, "crossings": crossings, "bends": bends, "length": length}
        results.append(row)
        full.append({**row, "_conn": c})
    results.sort(key=lambda r: r["score"], reverse=True)
    full.sort(key=lambda r: r["score"], reverse=True)
    return {"total": total, "connections": results, "connections_full": full}
```

Then replace the body of `score_wire_complexity` (from Task 3) with:

```python
def score_wire_complexity(sch_path, table_path, weights=None):
    sx = _parse_schematic(sch_path)
    project_dir = os.path.dirname(os.path.abspath(sch_path))
    pins = _collect_pins(sx, table_path, project_dir)
    sc = _score_from_ast(sx, pins, weights)
    return {"total": sc["total"], "connections": sc["connections"]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_wire_complexity.TestSimplify -v`
Expected: PASS (both `test_convert_lowers_total_and_preserves_net` and `test_dry_run_does_not_write`).

- [ ] **Step 6: Add rollback test for a multi-pin/branch net**

```python
class TestRollback(unittest.TestCase):
    def test_branch_net_not_converted(self):
        # A 3-pin net (T junction) has a branch node (degree 3) -> no clean pin-to-pin
        # chain is reconstructed, so nothing is converted and wires remain.
        td = tempfile.TemporaryDirectory()
        sch = os.path.join(td.name, "t.kicad_sch")
        table = os.path.join(td.name, "sym-lib-table")
        with open(table, "w") as f:
            f.write("(sym_lib_table)\n")
        sch_text = """(kicad_sch
  (version 20211123) (generator "eeschema") (generator_version "10.0")
  (uuid "u") (paper "A4")
  (lib_symbols
    (symbol "lib:P"
      (property "Reference" "P" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "P" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "P_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "K" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )
  (symbol (lib_id "lib:P") (at 50.8 50.8 0) (property "Reference" "A" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (symbol (lib_id "lib:P") (at 76.2 50.8 0) (property "Reference" "B" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (symbol (lib_id "lib:P") (at 63.5 63.5 0) (property "Reference" "C" (at 0 0 0)) (property "Value" "P" (at 0 0 0)))
  (wire (pts (xy 50.8 50.8) (xy 63.5 50.8)) (stroke (width 0) (type default)) (uuid "w1"))
  (wire (pts (xy 63.5 50.8) (xy 76.2 50.8)) (stroke (width 0) (type default)) (uuid "w2"))
  (wire (pts (xy 63.5 50.8) (xy 63.5 63.5)) (stroke (width 0) (type default)) (uuid "w3"))
)
"""
        with open(sch, "w") as f:
            f.write(sch_text)
        res = simplify_wires(sch, table, threshold=0.0)
        self.assertEqual(len(res["converted"]), 0)  # branch node => no clean pin-to-pin
        with open(sch) as f:
            self.assertIn("(wire", f.read())  # wires untouched
        td.cleanup()
```

- [ ] **Step 7: Run all tests**

Run: `uv run python -m unittest tests.test_wire_complexity -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add kicad_skill/wire_complexity.py tests/test_wire_complexity.py
git commit -m "feat(wire-complexity): label conversion with net-preservation gate + simplify_wires"
```

---

## Task 5: CLI `simplify-wires` subcommand

**Files:**
- Modify: `kicad_skill/main.py`

- [ ] **Step 1: Add the subparser**

In `main()`, after the `create-module` subparser block (around `main.py:258`), add:

```python
    # simplify-wires parser
    simp_parser = subparsers.add_parser("simplify-wires", help="Convert high-complexity wires to local labels")
    simp_parser.add_argument("--schematic", required=True, help="Path to the .kicad_sch file")
    simp_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")
    simp_parser.add_argument("--threshold", type=float, default=50.0, help="Total complexity target (default: 50)")
    simp_parser.add_argument("--max", type=int, default=None, dest="max_conversions", help="Max conversions")
    simp_parser.add_argument("--wc", type=float, default=10.0, help="Crossing weight (default: 10)")
    simp_parser.add_argument("--wb", type=float, default=2.0, help="Bend weight (default: 2)")
    simp_parser.add_argument("--wl", type=float, default=0.5, help="Length weight (default: 0.5)")
    simp_parser.add_argument("--dry-run", action="store_true", help="Report plan without writing")
```

- [ ] **Step 2: Add the dispatch + handler**

In `main()`'s command dispatch chain (after the `create-module` branch around `main.py:274`), add:

```python
    elif args.command == "simplify-wires":
        handle_simplify_wires(args)
```

Add the handler function near `handle_create_module` (around `main.py:170`):

```python
def handle_simplify_wires(args):
    table_path = args.table
    if not table_path:
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")
    from .wire_complexity import simplify_wires
    weights = {"crossings": args.wc, "bends": args.wb, "length": args.wl}
    try:
        res = simplify_wires(
            sch_path=args.schematic, table_path=table_path,
            threshold=args.threshold, weights=weights,
            max_conversions=args.max_conversions, dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error simplifying wires: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Total complexity: {res['total_before']:.1f} -> {res['total_after']:.1f}"
          + (" (dry-run)" if args.dry_run else ""))
    for c in res["converted"]:
        print(f"  CONVERTED {c['pin_a']} <-> {c['pin_b']} as label '{c['net_name']}' (score {c['score']:.1f})")
    for s in res["skipped_unsafe"]:
        print(f"  SKIPPED   {s['pin_a']} <-> {s['pin_b']}: {s['reason']}")
```

- [ ] **Step 3: Smoke-test the CLI**

Run:
```bash
uv run python -m kicad_skill.main simplify-wires --schematic /tmp/nope.kicad_sch --dry-run
```
Expected: exits non-zero with `Error simplifying wires: Schematic file /tmp/nope.kicad_sch not found`.

- [ ] **Step 4: Commit**

```bash
git add kicad_skill/main.py
git commit -m "feat(wire-complexity): add simplify-wires CLI subcommand"
```

---

## Task 6: `evaluate_layout` read-only metric

**Files:**
- Modify: `kicad_skill/evaluate_layout.py`
- Test: `tests/test_wire_complexity.py`

- [ ] **Step 1: Write the failing test**

```python
class TestEvalMetric(unittest.TestCase):
    def test_evaluate_reports_wire_complexity_total(self):
        td = tempfile.TemporaryDirectory()
        sch = os.path.join(td.name, "t.kicad_sch")
        table = os.path.join(td.name, "sym-lib-table")
        with open(table, "w") as f:
            f.write("(sym_lib_table)\n")
        with open(sch, "w") as f:
            f.write(SCH2)
        from kicad_skill.evaluate_layout import evaluate_schematic_layout
        res = evaluate_schematic_layout(sch, table)
        self.assertIn("wire_complexity_total", res)
        self.assertGreaterEqual(res["wire_complexity_total"], 0.0)
        td.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_wire_complexity.TestEvalMetric -v`
Expected: FAIL with `KeyError: 'wire_complexity_total'` / `assertIn` failure.

- [ ] **Step 3: Implement**

In `evaluate_layout.py`, in `evaluate_schematic_layout`, just before the `return {` (around
`evaluate_layout.py:430` after CHECK 6/7), add:

```python
    # Informational only — wire routing complexity (NOT fatal, does not affect score band).
    try:
        from .wire_complexity import score_wire_complexity
        wire_complexity_total = score_wire_complexity(sch_path, table_path)["total"]
    except Exception:
        wire_complexity_total = 0.0
```

Add the key to the returned dict:

```python
        "wire_complexity_total": wire_complexity_total,
```

And in the `__main__` report block, after the dangling line, add:

```python
    print(f"Wire complexity total: {res.get('wire_complexity_total', 0.0):.1f}")
```

- [ ] **Step 4: Run test + full suite**

Run: `uv run python -m unittest tests.test_wire_complexity -v`
Expected: PASS.

Run: `uv run python -m unittest discover -s tests`
Expected: OK (all prior tests still pass).

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/evaluate_layout.py tests/test_wire_complexity.py
git commit -m "feat(wire-complexity): surface wire_complexity_total in evaluate_layout"
```

---

## Task 7: End-to-end check on the CAN demo

**Files:** none (verification only)

- [ ] **Step 1: Run the demo + simplify dry-run**

```bash
uv run scratch/run_mcp_verification.py >/dev/null 2>&1
uv run python -m kicad_skill.main simplify-wires --schematic scratch/mcp_test/mcp_test.kicad_sch --dry-run
```
Expected: prints a total and a (possibly empty) conversion plan; the SPI signal connections
(U1↔U2) should rank highest by score.

- [ ] **Step 2: Apply + verify no shorts/dangling introduced**

```bash
uv run python -m kicad_skill.main simplify-wires --schematic scratch/mcp_test/mcp_test.kicad_sch --threshold 30
uv run kicad_skill/evaluate_layout.py scratch/mcp_test/mcp_test.kicad_sch scratch/mcp_test/sym-lib-table
```
Expected: `Net shorts (FATAL): 0`, `Dangling wires (FATAL): 0`, and a lower
`Wire complexity total` than before.

- [ ] **Step 3: Commit (if any doc/notes updated)**

No code change expected. If behavior differs from expectations, return to systematic-debugging.

---

## Self-Review

**Spec coverage:**
- Score per connection (crossings/bends/length) → Task 3. ✓
- Total metric → Task 3 (`total`), Task 6 (evaluate). ✓
- Iterative simplifier until below threshold → Task 4. ✓
- Net-preservation gate + rollback → Task 4 (`_pin_partition` compare). ✓
- Local-label conversion, naming (existing → pin-derived → NET_n) → Task 4 (`_net_name_for`). ✓
- Pin-to-pin unit → Task 2 (`_reconstruct_connections`). ✓
- CLI `simplify-wires` → Task 5. ✓
- Read-only `wire_complexity_total` in evaluate → Task 6. ✓
- Tests: monotonicity, crossing, preserve nets, decrease total, rollback, dry_run → Tasks 3,4. ✓

**Notes / intentional scope:**
- Multi-pin / branch nets are not converted (no clean two-terminal degree-2 chain); the
  gate is a second backstop. This realizes the spec's "naturally left alone" for VDD/GND.
- The stub direction in `_apply_conversion` is naive (left for pin_a, right for pin_b). If a
  stub lands on an occupied grid point, a follow-up refinement can pick the pin's true
  outward direction from its orientation; acceptable for v1 since the gate validates
  connectivity regardless.

**Placeholder scan:** none.
**Type consistency:** `score_wire_complexity`/`_score_from_ast` return `total` + `connections`
(+ `connections_full` internally); `simplify_wires` returns `total_before/total_after/
converted/skipped_unsafe`. Consistent across tasks.
