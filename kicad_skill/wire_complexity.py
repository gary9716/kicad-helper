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
