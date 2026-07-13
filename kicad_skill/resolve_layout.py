"""
Two-pass hierarchical layout resolver for KiCad schematics.

Pass 1 (local):  resolve Symbol-Symbol AABB overlaps *within* each
                 physically-connected cluster using Minimum Translation
                 Vectors (MTV) snapped to the routing grid.

Pass 2 (global): treat each fully-resolved cluster as a single rigid AABB;
                 resolve inter-cluster overlaps with the same MTV approach.

Wires are NOT moved — call `connect` afterwards to re-route.
"""

import math
import os
import sys
from collections import defaultdict

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.schematic import (
    load_sym_lib_table,
    find_symbol_definition,
    get_symbol_local_bbox,
    get_symbol_instance_transform,
    get_instance_aabb,
    BoundingBox,
)
from kicad_skill.module import get_symbol_pins_global


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _snap_up(v, grid):
    """Snap |v| up to the nearest grid multiple, preserving sign."""
    if v == 0:
        return 0.0
    return math.copysign(math.ceil(abs(v) / grid) * grid, v)


def _mtv(a: BoundingBox, b: BoundingBox, grid=2.54):
    """Minimum Translation Vector to move *b* out of *a*.

    Picks the axis with the smaller overlap (minimum effort).
    Returns (dx, dy) snapped to *grid*. Both zero → no overlap.
    """
    xo = min(a.xmax, b.xmax) - max(a.xmin, b.xmin)
    yo = min(a.ymax, b.ymax) - max(a.ymin, b.ymin)
    if xo <= 0 or yo <= 0:
        return 0.0, 0.0

    ca_x = (a.xmin + a.xmax) / 2
    cb_x = (b.xmin + b.xmax) / 2
    ca_y = (a.ymin + a.ymax) / 2
    cb_y = (b.ymin + b.ymax) / 2

    if xo <= yo:
        raw = xo if cb_x >= ca_x else -xo
        return _snap_up(raw, grid), 0.0
    else:
        raw = yo if cb_y >= ca_y else -yo
        return 0.0, _snap_up(raw, grid)


def _bbox_union(boxes):
    return BoundingBox(
        min(b.xmin for b in boxes),
        min(b.ymin for b in boxes),
        max(b.xmax for b in boxes),
        max(b.ymax for b in boxes),
    )


# ── Symbol extraction ─────────────────────────────────────────────────────────

def _text_bbox(text, x, y, angle, size=1.27):
    """Approximate bbox for a center-anchored KiCad text string."""
    w = len(text) * size * 0.7  # Newstroke char width factor
    h = size
    a = angle % 360
    if a < 1 or abs(a - 180) < 1:
        return BoundingBox(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    return BoundingBox(x - h / 2, y - w / 2, x + h / 2, y + w / 2)


def _prop_text_bboxes(node):
    """Return BoundingBox for visible Reference and Value properties of a symbol node."""
    boxes = []
    for s in node[1:]:
        if not (isinstance(s, list) and s[0] == 'property'):
            continue
        name = s[1] if len(s) > 1 else ''
        if name not in ('Reference', 'Value'):
            continue
        text = s[2] if len(s) > 2 else ''
        if not text or text.startswith('#'):
            continue
        hidden = any(
            isinstance(sub, list) and sub[0] == 'hide'
            for sub in s[1:]
        )
        if hidden:
            continue
        at = next((sub for sub in s[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at is None:
            continue
        px, py = float(at[1]), float(at[2])
        ang = float(at[3]) if len(at) > 3 else 0.0
        # Read font size from effects if present
        size = 1.27
        for sub in s[1:]:
            if isinstance(sub, list) and sub[0] == 'effects':
                for esub in sub[1:]:
                    if isinstance(esub, list) and esub[0] == 'font':
                        for fsub in esub[1:]:
                            if isinstance(fsub, list) and fsub[0] == 'size' and len(fsub) > 2:
                                size = float(fsub[2])
        boxes.append(_text_bbox(text, px, py, ang, size))
    return boxes


def _gk(x, y, grid=1.27):
    """Grid-quantised key for Union-Find on wire endpoints."""
    return (round(x / grid), round(y / grid))


def _extract_symbols(sch_sexpr, local_defs, lib_map, project_dir):
    """Return list of symbol dicts.  at_node and prop_at_nodes are live
    references into sch_sexpr so in-place edits are reflected on format."""
    symbols = []
    for node in sch_sexpr[1:]:
        if not isinstance(node, list) or node[0] != 'symbol':
            continue

        lib_id = next(
            (s[1] for s in node[1:] if isinstance(s, list) and s[0] == 'lib_id' and len(s) > 1),
            '',
        )
        ref = next(
            (s[2] for s in node[1:]
             if isinstance(s, list) and s[0] == 'property'
             and len(s) > 2 and s[1] == 'Reference'),
            '?',
        )
        at_node = next(
            (s for s in node[1:] if isinstance(s, list) and s[0] == 'at' and len(s) > 2),
            None,
        )
        if at_node is None:
            continue

        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(node)

        # All property (at ...) nodes — need co-movement
        prop_at_nodes = []
        for s in node[1:]:
            if isinstance(s, list) and s[0] == 'property':
                pat = next(
                    (sub for sub in s[1:]
                     if isinstance(sub, list) and sub[0] == 'at' and len(sub) > 2),
                    None,
                )
                if pat:
                    prop_at_nodes.append(pat)

        defn = local_defs.get(lib_id)
        if not defn and ':' in lib_id:
            ln, sn = lib_id.split(':', 1)
            defn = find_symbol_definition(ln, sn, lib_map, project_dir)

        local_bbox = (
            get_symbol_local_bbox(defn)
            if defn
            else BoundingBox(-2.54, -2.54, 2.54, 2.54)
        )
        bbox = get_instance_aabb(local_bbox, tx, ty, angle, mirror_x, mirror_y)
        pins = get_symbol_pins_global(node, defn) if defn else []
        pin_pts = [(p['x'], p['y']) for p in pins]
        all_boxes = [bbox]
        if pin_pts:
            all_boxes.append(BoundingBox(
                min(px for px, py in pin_pts), min(py for px, py in pin_pts),
                max(px for px, py in pin_pts), max(py for px, py in pin_pts),
            ))
        all_boxes.extend(_prop_text_bboxes(node))
        bbox = _bbox_union(all_boxes)

        symbols.append({
            'ref': ref,
            'lib_id': lib_id,
            'x': tx, 'y': ty, 'angle': angle,
            'mirror_x': mirror_x, 'mirror_y': mirror_y,
            'local_bbox': local_bbox,
            'bbox': bbox,
            'pins': pins,
            'pin_pts': pin_pts,
            'at_node': at_node,
            'prop_at_nodes': prop_at_nodes,
        })
    return symbols


# ── Cluster detection (Union-Find on wire topology) ───────────────────────────

def _pin_on_segment(px, py, x1, y1, x2, y2, tol=0.5):
    """True if point (px,py) lies on segment (x1,y1)-(x2,y2) (H or V only)."""
    if abs(y1 - y2) < tol:   # horizontal
        return (abs(py - y1) < tol
                and min(x1, x2) - tol <= px <= max(x1, x2) + tol)
    if abs(x1 - x2) < tol:   # vertical
        return (abs(px - x1) < tol
                and min(y1, y2) - tol <= py <= max(y1, y2) + tol)
    return False


def _build_clusters(symbols, wire_nodes):
    """Group symbol indices by physical wire connectivity.

    Two symbols land in the same cluster only if a wire path runs between
    their pins.  Handles pins that sit in the *middle* of a wire segment
    (not just at endpoints) — e.g. bypass caps tapped onto a power rail.

    Global labels with the same name do NOT merge clusters.
    """
    # ① Collect raw wire segment floats alongside UF on grid-keys
    uf: dict = {}
    segments: list = []   # [(x1,y1,x2,y2)] in mm, for mid-segment pin check

    def find(n):
        uf.setdefault(n, n)
        while uf[n] != n:
            uf[n] = uf[uf[n]]
            n = uf[n]
        return n

    def union(a, b):
        uf[find(a)] = find(b)

    for w in wire_nodes:
        pts_node = next(
            (s for s in w[1:] if isinstance(s, list) and s[0] == 'pts'), None
        )
        if not pts_node:
            continue
        pts_raw = [
            (float(xy[1]), float(xy[2]))
            for xy in pts_node[1:]
            if isinstance(xy, list) and xy[0] == 'xy'
        ]
        pts = [_gk(x, y) for x, y in pts_raw]
        for i in range(len(pts) - 1):
            union(pts[i], pts[i + 1])
            segments.append((*pts_raw[i], *pts_raw[i + 1]))

    # ② Map each symbol pin to the wire UF root it belongs to.
    #    A pin belongs to a root if:
    #      a) its grid-key is already in the UF (endpoint hit), OR
    #      b) it lies on the interior of a wire segment → use that segment's
    #         endpoint root so the pin joins the connected component.
    root_to_syms: dict = defaultdict(list)
    for idx, sym in enumerate(symbols):
        seen: set = set()
        for px, py in sym['pin_pts']:
            gkey = _gk(px, py)
            if gkey in uf:
                r = find(gkey)
            else:
                # Check if pin lies in the middle of any wire segment
                r = None
                for x1, y1, x2, y2 in segments:
                    if _pin_on_segment(px, py, x1, y1, x2, y2):
                        r = find(_gk(x1, y1))   # join left endpoint's root
                        break
                if r is None:
                    r = gkey                      # isolated pin, own root
            if r not in seen:
                seen.add(r)
                root_to_syms[r].append(idx)

    # ③ UF on symbol indices
    sym_uf = list(range(len(symbols)))

    def sfind(i):
        while sym_uf[i] != i:
            sym_uf[i] = sym_uf[sym_uf[i]]
            i = sym_uf[i]
        return i

    def sunion(i, j):
        sym_uf[sfind(i)] = sfind(j)

    for syms_at_root in root_to_syms.values():
        for k in range(1, len(syms_at_root)):
            sunion(syms_at_root[0], syms_at_root[k])

    cluster_map: dict = defaultdict(set)
    for i in range(len(symbols)):
        cluster_map[sfind(i)].add(i)

    return list(cluster_map.values())


# ── In-place symbol movement ──────────────────────────────────────────────────

def _move_symbol(sym, dx, dy):
    """Translate symbol position in both the Python dict and the live sexpr."""
    sym['x'] += dx
    sym['y'] += dy
    b = sym['bbox']
    sym['bbox'] = BoundingBox(b.xmin + dx, b.ymin + dy, b.xmax + dx, b.ymax + dy)
    sym['pin_pts'] = [(px + dx, py + dy) for px, py in sym['pin_pts']]

    n = sym['at_node']
    n[1] = f'{sym["x"]:.3f}'
    n[2] = f'{sym["y"]:.3f}'

    for pat in sym['prop_at_nodes']:
        pat[1] = f'{float(pat[1]) + dx:.3f}'
        pat[2] = f'{float(pat[2]) + dy:.3f}'


# ── Global label bounding boxes (immovable obstacles) ────────────────────────

_LABEL_H      = 3.81      # mm, full height of a global label box
_LABEL_CHAR_W = 1.524     # mm per character
_LABEL_BASE_W = 3.048     # mm fixed base (arrow + margin)


def _label_bbox(name, x, y, angle, justify='left'):
    """AABB of the text region of a global label.

    KiCad 10: for horizontal (0°/180°) labels, `justify` alone determines
    which direction the label extends from its `at` connection point:
      justify=left  → extends RIGHT (connection on left)
      justify=right → extends LEFT  (connection on right)
    The angle value (0° vs 180°) affects the arrow shape but NOT the bbox.

    For vertical (90°/270°) labels, angle determines extend direction:
      90°  → extends upward   (decreasing y)
      270° → extends downward (increasing y)
    """
    text_w = len(name) * _LABEL_CHAR_W + _LABEL_BASE_W
    half_h = _LABEL_H / 2
    a = angle % 360
    if abs(a - 90) < 1:   # 90°: extends upward
        return BoundingBox(x - half_h, y - text_w, x + half_h, y)
    if abs(a - 270) < 1:  # 270°: extends downward
        return BoundingBox(x - half_h, y, x + half_h, y + text_w)
    # 0° or 180°: direction from justify only
    if justify != 'right':
        return BoundingBox(x, y - half_h, x + text_w, y + half_h)   # extends right
    return BoundingBox(x - text_w, y - half_h, x, y + half_h)       # extends left


def _get_label_justify(node):
    """Extract justify value ('left' or 'right') from a global_label node."""
    effects = next(
        (s for s in node[1:] if isinstance(s, list) and s[0] == 'effects'),
        None,
    )
    if effects is None:
        return 'left'
    justify_node = next(
        (s for s in effects[1:] if isinstance(s, list) and s[0] == 'justify'),
        None,
    )
    if justify_node and len(justify_node) > 1 and justify_node[1] == 'right':
        return 'right'
    return 'left'


def _extract_label_obstacles(sch_sexpr):
    """Return list of BoundingBox for all global_label text regions."""
    obstacles = []
    for node in sch_sexpr[1:]:
        if not isinstance(node, list) or node[0] != 'global_label':
            continue
        name = node[1]
        at_node = next(
            (s for s in node[1:] if isinstance(s, list) and s[0] == 'at' and len(s) > 2),
            None,
        )
        if at_node is None:
            continue
        x = float(at_node[1])
        y = float(at_node[2])
        angle = float(at_node[3]) if len(at_node) > 3 else 0.0
        justify = _get_label_justify(node)
        obstacles.append(_label_bbox(name, x, y, angle, justify))
    return obstacles


# ── Sweep legalization ───────────────────────────────────────────────────────
#
# Replaces MTV iteration.  Sort items by xmin, process left-to-right.
# Each item jumps past the rightmost xmax of ALL y-overlapping predecessors
# in one move (no small steps → no oscillation).
# Repeat rounds until stable; at most n rounds needed.
# Items only ever move right, so xmin is monotonically non-decreasing → terminates.

def _overlaps_x_y(bi, bp):
    return (
        (min(bi.ymax, bp.ymax) - max(bi.ymin, bp.ymin)) > 0 and
        (min(bi.xmax, bp.xmax) - max(bi.xmin, bp.xmin)) > 0
    )


def _legalize(items, get_bbox, apply_move, grid=2.54, obstacles=None):
    """Sweep-based slot legalization.

    items      : list of opaque tokens
    get_bbox   : token -> BoundingBox
    apply_move : (token, dx, dy) -> None  (must update bbox in place)
    obstacles  : optional list of immovable BoundingBox (e.g. label text zones)
    Returns total number of moves applied.
    """
    n = len(items)
    total = 0

    for _round in range(n):       # at most n rounds; usually 1-2
        moved_this_round = False

        # Re-sort by left edge each round (items may have moved)
        order = sorted(range(n), key=lambda i: get_bbox(items[i]).xmin)

        for k in range(1, n):
            idx = order[k]
            bi = get_bbox(items[idx])

            # Rightmost xmax among all y-overlapping predecessors that also x-overlap
            clear_past = None
            for prev in range(k):
                pidx = order[prev]
                bp = get_bbox(items[pidx])
                if not _overlaps_x_y(bi, bp):
                    continue
                if clear_past is None or bp.xmax > clear_past:
                    clear_past = bp.xmax

            # Also clear immovable obstacles (global label text zones)
            if obstacles:
                for ob in obstacles:
                    if not _overlaps_x_y(bi, ob):
                        continue
                    if clear_past is None or ob.xmax > clear_past:
                        clear_past = ob.xmax

            if clear_past is not None:
                # Jump left edge to next grid multiple past clear_past
                target_xmin = math.ceil(clear_past / grid) * grid
                dx = target_xmin - bi.xmin
                if dx > 1e-6:
                    apply_move(items[idx], dx, 0.0)
                    total += 1
                    moved_this_round = True

        if not moved_this_round:
            break

    return total


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_schematic_layout(
    sch_path,
    table_path,
    max_iter=50,
    grid=2.54,
    dry_run=False,
    out_path=None,
):
    """Two-pass layout resolver.

    Returns dict:
        clusters     — number of physical clusters found
        pass1_moves  — MTV moves in intra-cluster pass
        pass2_moves  — moves in inter-cluster rigid-body pass
        remaining    — symbol pairs still overlapping after resolution
    """
    with open(sch_path, encoding='utf-8') as f:
        content = f.read()
    sch = parse_sexpr(content)

    project_dir = os.path.dirname(sch_path)
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}

    local_defs: dict = {}
    for child in sch[1:]:
        if isinstance(child, list) and child[0] == 'lib_symbols':
            for sym in child[1:]:
                if isinstance(sym, list) and sym[0] == 'symbol' and len(sym) > 1:
                    local_defs[sym[1]] = sym

    wire_nodes = [c for c in sch[1:] if isinstance(c, list) and c[0] == 'wire']
    symbols = _extract_symbols(sch, local_defs, lib_map, project_dir)
    clusters = _build_clusters(symbols, wire_nodes)
    label_obstacles = _extract_label_obstacles(sch)

    # Pre-compute: which passive each #PWR symbol is physically attached to.
    # (matched by proximity of power symbol position to passive pin position)
    # Power symbols are excluded from legalize and instead snapped to parent
    # passive pins AFTER the legalize so they always stay connected.
    SNAP_TOL = 1.27   # mm
    power_to_passive: dict = {}   # pwr_idx -> passive_idx
    for pi, ps in enumerate(symbols):
        if not ps['ref'].startswith('#PWR') and not ps['ref'].startswith('#FLG'):
            continue
        px, py = ps['x'], ps['y']
        for ci, cs in enumerate(symbols):
            if cs['ref'].startswith('#'):
                continue
            for pin_x, pin_y in cs['pin_pts']:
                if abs(pin_x - px) < SNAP_TOL and abs(pin_y - py) < SNAP_TOL:
                    power_to_passive[pi] = ci
                    break
            if pi in power_to_passive:
                break

    power_indices = set(power_to_passive.keys())

    # ── Pass 1: intra-cluster (exclude #PWR — snap them after) ───────────────
    pass1_moves = 0
    for cluster in clusters:
        # Exclude power symbols from legalize items
        clist = [i for i in cluster if i not in power_indices]
        if len(clist) < 2:
            continue

        def _gb1(idx, _syms=symbols):
            return _syms[idx]['bbox']

        def _mv1(idx, dx, dy, _syms=symbols):
            # Snap dx to 1.27mm grid so symbol center stays on-grid
            dx = math.ceil(dx / 1.27) * 1.27
            _move_symbol(_syms[idx], dx, dy)

        pass1_moves += _legalize(clist, _gb1, _mv1, grid, obstacles=label_obstacles)

    # Snap each #PWR to its parent passive's closest pin (post-legalize)
    for pi, ci in power_to_passive.items():
        ps = symbols[pi]
        cs = symbols[ci]
        closest = min(cs['pin_pts'], key=lambda p: (p[0] - ps['x'])**2 + (p[1] - ps['y'])**2)
        dx, dy = closest[0] - ps['x'], closest[1] - ps['y']
        if abs(dx) > 0.01 or abs(dy) > 0.01:
            _move_symbol(ps, dx, dy)

    # ── Pass 2: inter-cluster (rigid body) ───────────────────────────────────
    # Build cluster state objects: bbox (mutable) + member index list
    cluster_states = []
    for cluster in clusters:
        clist = list(cluster)
        cluster_states.append({
            'indices': clist,
            'bbox': _bbox_union([symbols[i]['bbox'] for i in clist]),
        })

    def _gb2(cs):
        return cs['bbox']

    def _mv2(cs, dx, dy, _syms=symbols):
        for idx in cs['indices']:
            _move_symbol(_syms[idx], dx, dy)
        b = cs['bbox']
        cs['bbox'] = BoundingBox(b.xmin + dx, b.ymin + dy, b.xmax + dx, b.ymax + dy)

    pass2_moves = _legalize(cluster_states, _gb2, _mv2, grid)

    # ── Count remaining symbol-symbol overlaps ────────────────────────────────
    remaining = 0
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            dx, dy = _mtv(symbols[i]['bbox'], symbols[j]['bbox'], grid=0.1)
            if dx != 0 or dy != 0:
                remaining += 1

    result = {
        'clusters': len(clusters),
        'pass1_moves': pass1_moves,
        'pass2_moves': pass2_moves,
        'remaining': remaining,
    }

    if not dry_run:
        dest = out_path or sch_path
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(format_sexpr(sch))

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser(description='Resolve layout overlaps in a KiCad schematic')
    p.add_argument('schematic')
    p.add_argument('table')
    p.add_argument('--dry-run', action='store_true', help='Compute moves but do not write')
    p.add_argument('--out', help='Output path (default: overwrite input)')
    p.add_argument('--max-iter', type=int, default=50)
    p.add_argument('--grid', type=float, default=2.54, help='Grid snap in mm (default 2.54)')
    args = p.parse_args()

    res = resolve_schematic_layout(
        args.schematic, args.table,
        max_iter=args.max_iter,
        grid=args.grid,
        dry_run=args.dry_run,
        out_path=args.out,
    )
    print(f"Clusters:      {res['clusters']}")
    print(f"Pass-1 moves:  {res['pass1_moves']}  (intra-cluster)")
    print(f"Pass-2 moves:  {res['pass2_moves']}  (inter-cluster rigid body)")
    print(f"Remaining:     {res['remaining']}  symbol-symbol overlaps")
    if res['remaining'] == 0:
        print("OK — no overlaps remaining.")
    else:
        print(f"WARNING: {res['remaining']} overlap(s) unresolved — increase --max-iter or check space.")
