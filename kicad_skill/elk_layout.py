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


def name_nets(nets, pin_positions, labels_at):
    """Attach a name to each anonymous pin-set net.

    A net whose any pin position carries an existing label uses that label's
    text; otherwise the name is synthesized from the first pin id (sorted),
    e.g. NET_U2_1. Returns (list of (name, pin_set), set of synthesized names)
    so callers can tell user-given names from invented ones.
    """
    named = []
    synthesized = set()
    for net in nets:
        name = None
        for pid in sorted(net):
            pos = pin_positions.get(pid)
            if pos is not None and pos in labels_at:
                name = labels_at[pos]
                break
        if name is None:
            base = "NET_" + sorted(net)[0].replace(":", "_")
            name = base
            taken = set(labels_at.values()) | {n for n, _ in named}
            suffix = 1
            while name in taken:
                name = f"{base}_{suffix}"
                suffix += 1
            synthesized.add(name)
        named.append((name, net))
    return named, synthesized


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
        # ELK layered rejects hyperedges ("Passed edge is not 'simple'"):
        # split each net into a star of simple 2-pin edges from one hub pin.
        ordered = sorted(pins)
        for j, target in enumerate(ordered[1:]):
            edges.append({
                "id": f"e{i}_{j}_{name}",
                "sources": [ordered[0]],
                "targets": [target],
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
    seen = set()
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
                # star-split edges of one net share the hub pin, so their
                # initial runs coincide — drop exact duplicate segments
                key = frozenset((a, b))
                if a != b and key not in seen:
                    seen.add(key)
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


import uuid as _uuid

from .parser import parse_sexpr, format_sexpr
from .resolve_layout import _extract_symbols, _move_symbol
from .netlist_eval import extract_actual_netlist, compare
from .regenerate import _label_orientation, _make_label
from .schematic import load_sym_lib_table


# Label footprint model (matches evaluate_layout's LABEL-OVERLAP check):
# height 3.81mm, width ~ chars*1.5 + 3mm, extending right/up/left/down for
# angle 0/90/180/270. Adjacent pins of one high-fanout net (2.54mm pitch)
# always collide at a shared angle, so orientation must be chosen per label.
_LABEL_H, _LABEL_CHAR_W, _LABEL_BASE_W = 3.81, 1.5, 3.0
_JUSTIFY = {0: "left", 90: "left", 180: "right", 270: "right"}


def _label_bbox(x, y, angle, name):
    w, h = len(name) * _LABEL_CHAR_W + _LABEL_BASE_W, _LABEL_H
    if angle == 0:
        return (x, y - h / 2, x + w, y + h / 2)
    if angle == 180:
        return (x - w, y - h / 2, x, y + h / 2)
    if angle == 90:   # extends up (y decreasing)
        return (x - h / 2, y - w, x + h / 2, y)
    return (x - h / 2, y, x + h / 2, y + w)  # 270: extends down


def _overlap_area(b1, b2):
    xo = min(b1[2], b2[2]) - max(b1[0], b2[0])
    yo = min(b1[3], b2[3]) - max(b1[1], b2[1])
    return xo * yo if xo > 0 and yo > 0 else 0.0


def _place_labels(label_nets, moved_pins, centers):
    """Yield (name, x, y, angle, justify) per labeled pin, greedily rotating
    each label to the first orientation that doesn't collide (>2mm² overlap,
    the evaluator's threshold) with labels already placed. Preference order
    starts pointing away from the symbol body."""
    placed = []
    out = []
    for name, pins in sorted(label_nets):
        for pid in sorted(pins):
            x, y = moved_pins[pid]
            cx, cy = centers.get(pid.split(":")[0], (x, y))
            dx, dy = x - cx, y - cy
            if abs(dx) >= abs(dy):
                outward = 0 if dx >= 0 else 180
            else:
                outward = 90 if dy < 0 else 270  # KiCad y grows downward
            candidates = [outward, (outward + 90) % 360,
                          (outward + 270) % 360, (outward + 180) % 360]
            best = None  # (max_overlap, angle, bbox)
            for angle in candidates:
                bbox = _label_bbox(x, y, angle, name)
                worst = max((_overlap_area(bbox, p) for p in placed), default=0.0)
                if worst <= 2.0:
                    best = (worst, angle, bbox)
                    break
                if best is None or worst < best[0]:
                    best = (worst, angle, bbox)
            _, angle, bbox = best
            placed.append(bbox)
            out.append((name, x, y, angle, _JUSTIFY[angle]))
    return out


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
    (zero shorts/opens). The result is written to a temp file first and only
    renamed onto out_path when the gate passes; on failure out_path is left
    untouched and report["rejected_file"] points at the rejected layout.
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
    named, synthesized = name_nets(
        [n for n in raw_nets if len(n) >= 2], pin_positions, labels_at)
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
    edge_by_id = {f"e{i}_{j}_{name}": (name, pins)
                  for i, (name, pins) in enumerate(edge_nets)
                  for j in range(max(len(pins) - 1, 0))}
    fallback_named = set()
    for edge in unrouted:
        net = edge_by_id.get(edge["id"])
        if net and net[0] not in fallback_named:
            fallback_named.add(net[0])
            label_nets.append(net)
    segments = derive_wires(routed, moved_pins)
    junctions = find_junctions(segments)

    # strip ALL old wires, junctions, and old labels (full re-route)
    sch[:] = [c for c in sch if not (
        isinstance(c, list) and c and c[0] in ("wire", "junction", "label"))]

    centers = {s["ref"]: ((s["bbox"].xmin + s["bbox"].xmax) / 2,
                          (s["bbox"].ymin + s["bbox"].ymax) / 2) for s in symbols}
    for name, x, y, angle, justify in _place_labels(label_nets, moved_pins, centers):
        sch.append(_make_label(name, x, y, angle, justify))
    # edge-nets routed as wires lose their label element in the strip above;
    # re-emit user-given (non-synthesized) names at one pin so they survive
    user_labels = 0
    label_names = {n for n, _ in label_nets}
    for name, pins in edge_nets:
        if name in synthesized or name in label_names:
            continue
        pid = sorted(pins)[0]
        x, y = moved_pins[pid]
        cx, cy = centers.get(pid.split(":")[0], (x, y))
        angle, justify = _label_orientation(x, y, cx, cy)
        sch.append(_make_label(name, x, y, angle, justify))
        user_labels += 1
    for a, b in segments:
        sch.append(_make_wire(a, b))
    for pt in junctions:
        sch.append(_make_junction(pt))

    # write to a temp file in the SAME directory (sub-sheet paths resolve
    # relative to the schematic) and gate before touching out_path
    tmp_path = out_path + ".elk_tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(sch))

    rep = compare(extract_actual_netlist(tmp_path, table_path), gt)
    result = {"ok": not rep["fatal"], "report": rep, "deltas": deltas,
              "wires": len(segments),
              "labels": sum(len(p) for _, p in label_nets) + user_labels,
              "junctions": len(junctions)}
    if result["ok"]:
        os.replace(tmp_path, out_path)
    else:
        result["rejected_file"] = tmp_path
    return result
