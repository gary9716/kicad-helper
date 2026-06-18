"""Ground-truth netlist evaluation.

The geometry-only evaluator (evaluate_layout.py) scores how a schematic *looks*
and catches some local wiring defects, but it cannot judge logical correctness:
once two distinct logical nets are wired together they look like one net and the
score stays 100. This module compares the *actual* electrical connectivity of a
schematic against a user-confirmed ground-truth netlist (see the
plan-ground-truth-netlist skill) and reports shorts/opens/missing as hard
failures.

Pin identity is "Ref:PinNumber" (e.g. "U2:18"), matching the ground-truth JSON.
"""
import json
import os
import sys

# Ensure package path is visible when run as a script.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr
from kicad_skill.schematic import (
    load_sym_lib_table,
    find_symbol_definition,
)
from kicad_skill.module import get_symbol_pins_global


def grid_key(x, y):
    return (int(round(x / 1.27)), int(round(y / 1.27)))


def load_ground_truth(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Comparison (pure logic over connectivity sets)
# --------------------------------------------------------------------------
def compare(actual_nets, gt_nets):
    """Compare actual electrical nets against the ground truth.

    actual_nets: list of sets of pin ids ("Ref:Num"); each set is one
                 electrical net as the schematic actually wires it.
    gt_nets:     list of {"name", "pins"} from the ground-truth JSON.

    Returns a report dict. Shorts (>1 GT net fused into one actual net) and
    opens (one GT net split across actual nets) are FATAL: the netlist is wrong.
    """
    pin_to_gt = {}
    for g in gt_nets:
        for pin in g["pins"]:
            pin_to_gt[pin] = g["name"]

    pin_to_actual = {}
    for i, net in enumerate(actual_nets):
        for pin in net:
            pin_to_actual[pin] = i

    missing = sorted(p for p in pin_to_gt if p not in pin_to_actual)

    # Shorts: an actual net carrying pins from more than one GT net.
    shorts = []
    for net in actual_nets:
        names = sorted({pin_to_gt[p] for p in net if p in pin_to_gt})
        if len(names) > 1:
            shorts.append({
                "gt_nets": names,
                "pins": sorted(p for p in net if p in pin_to_gt),
            })

    # Opens: a GT net whose present pins land on more than one actual net.
    opens = []
    for g in gt_nets:
        present = [p for p in g["pins"] if p in pin_to_actual]
        idxs = sorted({pin_to_actual[p] for p in present})
        if len(idxs) > 1:
            fragments = [
                sorted(p for p in present if pin_to_actual[p] == idx)
                for idx in idxs
            ]
            opens.append({"gt_net": g["name"], "fragments": fragments})

    # Extra: an actual net of >=2 pins, none of which appear in the ground truth.
    extra = []
    for net in actual_nets:
        if len(net) >= 2 and not any(p in pin_to_gt for p in net):
            extra.append({"pins": sorted(net)})

    short_names = {n for s in shorts for n in s["gt_nets"]}
    open_names = {o["gt_net"] for o in opens}
    missing_names = {pin_to_gt[p] for p in missing}
    ok = [
        g["name"] for g in gt_nets
        if g["name"] not in short_names
        and g["name"] not in open_names
        and g["name"] not in missing_names
    ]

    fatal = bool(shorts or opens)
    return {
        "shorts": shorts,
        "opens": opens,
        "missing": missing,
        "extra": extra,
        "ok": ok,
        "fatal": fatal,
    }


# --------------------------------------------------------------------------
# Actual netlist extraction (KiCad-faithful, hierarchy-flattened)
# --------------------------------------------------------------------------
class _UF:
    def __init__(self):
        self.parent = {}

    def find(self, n):
        self.parent.setdefault(n, n)
        while self.parent[n] != n:
            self.parent[n] = self.parent[self.parent[n]]
            n = self.parent[n]
        return n

    def union(self, a, b):
        self.parent.setdefault(a, a)
        self.parent.setdefault(b, b)
        self.parent[self.find(a)] = self.find(b)


def _collinear_overlap(s1, s2):
    """True if two grid segments lie on the same line and overlap/touch."""
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2
    if x1 == x2 == x3 == x4:
        lo1, hi1 = sorted((y1, y2))
        lo2, hi2 = sorted((y3, y4))
        return min(hi1, hi2) >= max(lo1, lo2)
    if y1 == y2 == y3 == y4:
        lo1, hi1 = sorted((x1, x2))
        lo2, hi2 = sorted((x3, x4))
        return min(hi1, hi2) >= max(lo1, lo2)
    return False


def _on_segment(pt, seg):
    """True if grid point pt lies on grid segment seg (endpoints included)."""
    (ax, ay), (bx, by) = seg
    px, py = pt
    if ax == bx == px and min(ay, by) <= py <= max(ay, by):
        return True
    if ay == by == py and min(ax, bx) <= px <= max(ax, bx):
        return True
    return False


def _parse_sheet(content):
    """Parse one schematic sheet into its connectivity primitives.

    Returns a dict with pins (id -> [grid_keys]), wire segments, junction gks,
    local/global/hierarchical label name -> grid_keys, and child sheet specs.
    """
    sexpr = parse_sexpr(content)

    local_definitions = {}
    for child in sexpr[1:]:
        if isinstance(child, list) and child and child[0] == "lib_symbols":
            for d in child[1:]:
                if isinstance(d, list) and d[0] == "symbol" and len(d) > 1:
                    local_definitions[d[1]] = d

    pins = {}            # "Ref:Num" -> list of grid_key
    segs = []            # list of (gk, gk)
    junctions = set()
    local_labels = {}    # name -> [gk]
    global_labels = {}   # name -> [gk]
    hier_labels = {}     # name -> [gk]
    sheets = []          # list of {"file": str, "pins": {name: gk}}

    def _at(node):
        a = next((s for s in node[1:] if isinstance(s, list) and s[0] == "at"), None)
        return (float(a[1]), float(a[2])) if a else None

    for child in sexpr[1:]:
        if not isinstance(child, list) or not child:
            continue
        tag = child[0]
        if tag == "symbol":
            ref, lib_id = "", ""
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == "lib_id":
                        lib_id = sub[1]
                    elif sub[0] == "property" and len(sub) > 2 and sub[1] == "Reference":
                        ref = sub[2]
            defn = local_definitions.get(lib_id)
            for p in get_symbol_pins_global(child, defn):
                pins.setdefault(f"{ref}:{p['number']}", []).append(
                    grid_key(p["x"], p["y"]))
        elif tag == "wire":
            pts = next((s for s in child[1:] if isinstance(s, list) and s[0] == "pts"), None)
            if pts:
                xy = [(float(p[1]), float(p[2])) for p in pts[1:]
                      if isinstance(p, list) and p[0] == "xy"]
                if len(xy) >= 2:
                    segs.append((grid_key(*xy[0]), grid_key(*xy[-1])))
        elif tag == "junction":
            at = _at(child)
            if at:
                junctions.add(grid_key(*at))
        elif tag == "label":
            at = _at(child)
            if at:
                local_labels.setdefault(child[1], []).append(grid_key(*at))
        elif tag == "global_label":
            at = _at(child)
            if at:
                global_labels.setdefault(child[1], []).append(grid_key(*at))
        elif tag == "hierarchical_label":
            at = _at(child)
            if at:
                hier_labels.setdefault(child[1], []).append(grid_key(*at))
        elif tag == "sheet":
            sfile = ""
            spins = {}
            for sub in child[1:]:
                if not isinstance(sub, list) or not sub:
                    continue
                if sub[0] == "property" and len(sub) > 2 and sub[1] == "Sheetfile":
                    sfile = sub[2]
                elif sub[0] == "pin" and len(sub) > 1:
                    at = _at(sub)
                    if at:
                        spins[sub[1]] = grid_key(*at)
            sheets.append({"file": sfile, "pins": spins})

    return {
        "pins": pins, "segs": segs, "junctions": junctions,
        "local_labels": local_labels, "global_labels": global_labels,
        "hier_labels": hier_labels, "sheets": sheets,
    }


def _union_sheet(uf, scope, data):
    """Union connectivity inside one sheet's scope. Returns nothing; mutates uf.

    Scope namespaces grid keys so identical coordinates on different sheets do
    not collide. KiCad-faithful: wires connect by shared endpoint, by collinear
    overlap (the accidental-merge failure mode), and pins/labels connect to a
    wire they lie on. Bare crossings only connect through a junction.
    """
    def node(gk):
        return (scope, gk)

    segs = data["segs"]
    for a, b in segs:
        uf.union(node(a), node(b))

    # Collinear-overlapping segments are electrically one wire.
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if _collinear_overlap(segs[i], segs[j]):
                uf.union(node(segs[i][0]), node(segs[j][0]))

    # Junctions tie any segment passing through the junction point.
    for jgk in data["junctions"]:
        touching = [s for s in segs if _on_segment(jgk, s)]
        for s in touching:
            uf.union(node(jgk), node(s[0]))

    # Pins/labels connect to a wire they lie on (KiCad needs no junction for a
    # pin or label endpoint sitting on a wire).
    connectors = []
    for gks in data["pins"].values():
        connectors.extend(gks)
    for gks in data["local_labels"].values():
        connectors.extend(gks)
    for gks in data["hier_labels"].values():
        connectors.extend(gks)
    for gks in data["global_labels"].values():
        connectors.extend(gks)
    for cgk in connectors:
        for s in segs:
            if _on_segment(cgk, s):
                uf.union(node(cgk), node(s[0]))

    # Same-named local labels within a sheet are one net.
    for gks in data["local_labels"].values():
        for g in gks[1:]:
            uf.union(node(gks[0]), node(g))


def extract_actual_netlist(root_path, table_path):
    """Flatten a (possibly hierarchical) schematic into actual electrical nets.

    Returns a list of sets of pin ids ("Ref:Num"). Parent sheet pins are tied to
    sub-sheet hierarchical labels by name; global labels are tied across all
    sheets by name. Reproduces KiCad connectivity so accidental net merges
    (shorts) survive into the comparison.
    """
    uf = _UF()
    project_dir = os.path.dirname(root_path)
    pin_nodes = []        # (scope, gk, pin_id)
    global_label_nodes = {}  # name -> [(scope, gk)]

    # Worklist of (scope, absolute file path). Root scope is "/".
    with open(root_path, "r", encoding="utf-8") as f:
        root_data = _parse_sheet(f.read())
    worklist = [("/", root_data)]
    sheet_counter = 0

    parsed = []  # (scope, data)
    while worklist:
        scope, data = worklist.pop(0)
        parsed.append((scope, data))
        for sh in data["sheets"]:
            sheet_counter += 1
            child_scope = f"{scope}sheet{sheet_counter}/"
            fpath = os.path.join(project_dir, sh["file"])
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                child_data = _parse_sheet(f.read())
            child_data["_parent_scope"] = scope
            child_data["_sheet_pins"] = sh["pins"]
            child_data["_scope"] = child_scope
            worklist.append((child_scope, child_data))

    # Union connectivity within every sheet.
    for scope, data in parsed:
        _union_sheet(uf, scope, data)
        for pid, gks in data["pins"].items():
            for gk in gks:
                pin_nodes.append((scope, gk, pid))
        for name, gks in data["global_labels"].items():
            for gk in gks:
                global_label_nodes.setdefault(name, []).append((scope, gk))

    # Tie sheet pins (parent) to hierarchical labels (child) by matching name.
    for scope, data in parsed:
        if "_sheet_pins" not in data:
            continue
        parent_scope = data["_parent_scope"]
        for name, parent_gk in data["_sheet_pins"].items():
            for child_gk in data["hier_labels"].get(name, []):
                uf.union((parent_scope, parent_gk), (data["_scope"], child_gk))

    # Tie identical global labels across all sheets.
    for name, nodes in global_label_nodes.items():
        for n in nodes[1:]:
            uf.union(nodes[0], n)

    # Group pins by net root.
    nets = {}
    for scope, gk, pid in pin_nodes:
        root = uf.find((scope, gk))
        nets.setdefault(root, set()).add(pid)
    return list(nets.values())


# --------------------------------------------------------------------------
# Orchestration + CLI
# --------------------------------------------------------------------------
def check_netlist(root_path, gt_path, table_path):
    gt = load_ground_truth(gt_path)
    actual = extract_actual_netlist(root_path, table_path)
    return compare(actual, gt["nets"]), gt


def _print_report(rep, gt):
    print("\n==========================================")
    print("        NETLIST CORRECTNESS REPORT        ")
    print("==========================================")
    verdict = "FATAL — NETLIST INVALID" if rep["fatal"] else "OK"
    print(f"VERDICT: {verdict}")
    print(f"GT nets: {len(gt['nets'])}   OK: {len(rep['ok'])}   "
          f"Shorts: {len(rep['shorts'])}   Opens: {len(rep['opens'])}   "
          f"Missing pins: {len(rep['missing'])}")
    print("------------------------------------------")
    for s in rep["shorts"]:
        print(f"  [SHORT]  {' <-> '.join(s['gt_nets'])}  via {', '.join(s['pins'])}")
    for o in rep["opens"]:
        frags = " | ".join("{" + ", ".join(f) + "}" for f in o["fragments"])
        print(f"  [OPEN]   {o['gt_net']} split: {frags}")
    if rep["missing"]:
        print(f"  [MISSING] pins not connected/placed: {', '.join(rep['missing'])}")
    for e in rep["extra"]:
        print(f"  [EXTRA]  unexpected net: {', '.join(e['pins'])}")
    if not (rep["shorts"] or rep["opens"] or rep["missing"] or rep["extra"]):
        print("  All ground-truth nets verified.")
    print("==========================================\n")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="check-netlist",
        description="Compare a schematic's actual connectivity to a ground-truth netlist.")
    parser.add_argument("--schematic", required=True, help="root .kicad_sch")
    parser.add_argument("--ground-truth", required=True, help="ground-truth JSON")
    parser.add_argument("--table", default="", help="sym-lib-table path")
    args = parser.parse_args(argv)

    rep, gt = check_netlist(args.schematic, args.ground_truth, args.table)
    _print_report(rep, gt)
    return 1 if rep["fatal"] else 0


if __name__ == "__main__":
    sys.exit(main())
