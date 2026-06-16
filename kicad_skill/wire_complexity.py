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
    by_text = {}
    for _, text, gk in _collect_labels(sx):
        by_text.setdefault(text, []).append(gk)
    for coords in by_text.values():
        for c in coords[1:]:
            union(coords[0], c)
    return find


def _adjacency(wires):
    """gk -> list of (neighbor_gk, wire_node)."""
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
    seen = set()
    for start_gk, start_pin in pin_gks.items():
        for nbr, w0 in adj.get(start_gk, []):
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
                    break
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
