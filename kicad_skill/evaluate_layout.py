import os
import sys
import math

# Ensure package path is visible
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr
from kicad_skill.schematic import (
    load_sym_lib_table,
    find_symbol_definition,
    get_symbol_local_bbox,
    get_symbol_instance_transform,
    get_instance_aabb,
    BoundingBox
)
from kicad_skill.module import get_symbol_pins_global

def evaluate_schematic_layout(sch_path, table_path):
    if not os.path.exists(sch_path):
        return {"error": f"Schematic file {sch_path} not found"}
        
    with open(sch_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)
    
    project_dir = os.path.dirname(sch_path)
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    
    # 1. Load symbol definitions
    lib_symbols = []
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'lib_symbols':
            lib_symbols = child
            break
            
    local_definitions = {}
    if lib_symbols:
        for child in lib_symbols[1:]:
            if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1:
                local_definitions[child[1]] = child

    # Parse symbols, wires, junctions, labels, and sheets
    symbols = []
    wires = []
    junctions = []
    labels = []
    sheets = []
    no_connects = []
    
    for child in sch_sexpr[1:]:
        if not isinstance(child, list) or not child:
            continue
        tag = child[0]
        if tag == 'symbol':
            symbols.append(child)
        elif tag == 'wire':
            wires.append(child)
        elif tag == 'junction':
            junctions.append(child)
        elif tag in ('label', 'global_label', 'hierarchical_label'):
            labels.append(child)
        elif tag == 'sheet':
            sheets.append(child)
        elif tag == 'no_connect':
            no_connects.append(child)

    issues = []
    deductions = 0
    
    # --- CHECK 1: Grid Snapping (Max Deduction: 30) ---
    grid_errors = 0
    
    def check_grid(x, y, desc):
        nonlocal grid_errors
        # Snap tolerance
        rx = abs(x) % 1.27
        ry = abs(y) % 1.27
        rx = min(rx, 1.27 - rx)
        ry = min(ry, 1.27 - ry)
        if rx > 0.01 or ry > 0.01:
            grid_errors += 1
            if grid_errors <= 10:  # limit reporting to first 10
                issues.append(f"[GRID] {desc} at ({x:.3f}, {y:.3f}) is not aligned to 1.27mm grid")

    # Check symbol placements
    for inst in symbols:
        ref = ""
        lib_id = ""
        tx, ty = 0.0, 0.0
        for sub in inst[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == 'lib_id':
                    lib_id = sub[1]
                elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                    ref = sub[2]
                elif sub[0] == 'at' and len(sub) > 2:
                    tx, ty = float(sub[1]), float(sub[2])
        check_grid(tx, ty, f"Symbol {ref} ({lib_id})")

    # Check sheet placements and sheet pins
    for sheet in sheets:
        sheet_name = ""
        tx, ty = 0.0, 0.0
        for sub in sheet[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Sheetname':
                    sheet_name = sub[2]
                elif sub[0] == 'at' and len(sub) > 2:
                    tx, ty = float(sub[1]), float(sub[2])
                elif sub[0] == 'pin' and len(sub) > 1:
                    pin_name = sub[1]
                    at_node = next((s for s in sub[1:] if isinstance(s, list) and s[0] == 'at'), None)
                    if at_node:
                        check_grid(float(at_node[1]), float(at_node[2]), f"Sheet pin {pin_name} of {sheet_name}")
        check_grid(tx, ty, f"Sheet {sheet_name}")

    # Check wire endpoints
    for w in wires:
        pts_node = next((sub for sub in w[1:] if isinstance(sub, list) and sub[0] == 'pts'), None)
        if pts_node:
            for xy in pts_node[1:]:
                if isinstance(xy, list) and len(xy) > 2 and xy[0] == 'xy':
                    check_grid(float(xy[1]), float(xy[2]), "Wire coordinate")

    # Check junctions
    for j in junctions:
        at_node = next((sub for sub in j[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            check_grid(float(at_node[1]), float(at_node[2]), "Junction")

    # Check labels
    for l in labels:
        at_node = next((sub for sub in l[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            check_grid(float(at_node[1]), float(at_node[2]), f"Label '{l[1]}'")

    if grid_errors > 0:
        deductions += min(grid_errors * 3, 30)
        issues.append(f"[GRID] Summary: Found {grid_errors} elements off-grid.")

    # --- CHECK 2: Symbol Overlaps (Max Deduction: 25) ---
    symbol_bboxes = []
    for inst in symbols:
        ref = ""
        lib_id = ""
        for sub in inst[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == 'lib_id':
                    lib_id = sub[1]
                elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                    ref = sub[2]
        defn = local_definitions.get(lib_id)
        if not defn and ':' in lib_id:
            lib_name, sym_name = lib_id.split(':', 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst)
        global_bbox = get_instance_aabb(local_bbox, tx, ty, angle, mirror_x, mirror_y)
        symbol_bboxes.append((ref, global_bbox))

    overlaps_count = 0
    for i in range(len(symbol_bboxes)):
        for j in range(i + 1, len(symbol_bboxes)):
            ref1, box1 = symbol_bboxes[i]
            ref2, box2 = symbol_bboxes[j]
            # Check overlap with a small tolerance (to allow touching margins)
            if not (box1.xmax - 0.5 < box2.xmin + 0.5 or box1.xmin + 0.5 > box2.xmax - 0.5 or
                    box1.ymax - 0.5 < box2.ymin + 0.5 or box1.ymin + 0.5 > box2.ymax - 0.5):
                overlaps_count += 1
                issues.append(f"[OVERLAP] Symbol {ref1} overlaps with symbol {ref2}")

    if overlaps_count > 0:
        deductions += min(overlaps_count * 10, 25)

    # --- CHECK 3: Open / Dangling Connections (Max Deduction: 25) ---
    pins = []
    for inst in symbols:
        ref = ""
        lib_id = ""
        for sub in inst[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == 'lib_id':
                    lib_id = sub[1]
                elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                    ref = sub[2]
        defn = local_definitions.get(lib_id)
        if not defn and ':' in lib_id:
            lib_name, sym_name = lib_id.split(':', 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        sym_pins = get_symbol_pins_global(inst, defn)
        for p in sym_pins:
            pins.append({
                'ref': ref,
                'number': p['number'],
                'name': p['name'],
                'x': p['x'],
                'y': p['y']
            })

    # Add sheet pins to pins to verify they are connected too
    for sheet in sheets:
        sheet_name = ""
        for sub in sheet[1:]:
            if isinstance(sub, list) and len(sub) > 2 and sub[0] == 'property' and sub[1] == 'Sheetname':
                sheet_name = sub[2]
        for sub in sheet[1:]:
            if isinstance(sub, list) and len(sub) > 0 and sub[0] == 'pin':
                pin_name = sub[1]
                at_node = next((s for s in sub[1:] if isinstance(s, list) and s[0] == 'at'), None)
                if at_node:
                    pins.append({
                        'ref': f"Sheet:{sheet_name}",
                        'number': pin_name,
                        'name': pin_name,
                        'x': float(at_node[1]),
                        'y': float(at_node[2])
                    })

    # Extract all wire coordinate lists
    wire_paths = []
    all_wire_ends = []
    for w in wires:
        pts_node = next((sub for sub in w[1:] if isinstance(sub, list) and sub[0] == 'pts'), None)
        if pts_node:
            pts = []
            for xy in pts_node[1:]:
                if isinstance(xy, list) and len(xy) > 2 and xy[0] == 'xy':
                    pts.append((float(xy[1]), float(xy[2])))
            if len(pts) >= 2:
                wire_paths.append(pts)
                all_wire_ends.append(pts[0])
                all_wire_ends.append(pts[-1])

    junction_pts = []
    for j in junctions:
        at_node = next((sub for sub in j[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            junction_pts.append((float(at_node[1]), float(at_node[2])))

    # Label positions: a pin coincident with a local/global/hierarchical label is
    # joined to that net by name, with no wire — KiCad treats it as connected.
    label_pts = []
    for l in labels:
        at_node = next((sub for sub in l[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            label_pts.append((float(at_node[1]), float(at_node[2])))

    # no_connect positions: pins here are intentionally unconnected — suppress DISCONNECT
    no_connect_pts = []
    for nc in no_connects:
        at_node = next((sub for sub in nc[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            no_connect_pts.append((float(at_node[1]), float(at_node[2])))

    # Power symbol pins: placed at the symbol's (at x y) position (pin length=0 at origin)
    for sym in symbols:
        lib_id_val = next((sub[1] for sub in sym[1:] if isinstance(sub, list) and sub[0] == 'lib_id' and len(sub) > 1), None)
        if lib_id_val and lib_id_val.startswith('power:'):
            at_node = next((sub for sub in sym[1:] if isinstance(sub, list) and sub[0] == 'at' and len(sub) > 2), None)
            if at_node:
                label_pts.append((float(at_node[1]), float(at_node[2])))

    unconnected_pins_count = 0
    for p in pins:
        px, py = p['x'], p['y']
        connected = False

        # Explicitly marked no_connect — not a real disconnect
        if any(abs(nx - px) < 0.05 and abs(ny - py) < 0.05 for nx, ny in no_connect_pts):
            continue

        # Check labels coincident with the pin
        for lx, ly in label_pts:
            if abs(lx - px) < 0.05 and abs(ly - py) < 0.05:
                connected = True
                break

        # Check wire endpoints
        if not connected:
            for wx, wy in all_wire_ends:
                if abs(wx - px) < 0.05 and abs(wy - py) < 0.05:
                    connected = True
                    break
                
        # Check wire pass-through (if it lies on a wire segment)
        if not connected:
            for path in wire_paths:
                for idx in range(len(path) - 1):
                    x1, y1 = path[idx]
                    x2, y2 = path[idx+1]
                    if min(x1, x2) - 0.05 <= px <= max(x1, x2) + 0.05 and min(y1, y2) - 0.05 <= py <= max(y1, y2) + 0.05:
                        if abs((x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)) < 0.1:
                            connected = True
                            break
                if connected:
                    break
                    
        if not connected:
            # Near-miss: wire end within 0.1–4mm of pin → likely coord offset bug
            near = min(
                (((wx - px)**2 + (wy - py)**2)**0.5, wx, wy)
                for wx, wy in all_wire_ends
            ) if all_wire_ends else None
            if near and 0.1 < near[0] < 4.0:
                issues.append(f"[WARN:NEAR-MISS] Wire end at ({near[1]:.3f},{near[2]:.3f}) is {near[0]:.2f}mm from pin {p['number']} ({p['name']}) of {p['ref']} at ({p['x']:.3f},{p['y']:.3f}) — check y-axis or rotation")
            unconnected_pins_count += 1
            issues.append(f"[DISCONNECT] Pin {p['number']} ({p['name']}) of {p['ref']} at ({p['x']:.3f}, {p['y']:.3f}) is unconnected")

    if unconnected_pins_count > 0:
        deductions += min(unconnected_pins_count * 5, 25)

    # --- CHECK 4: Layout Density and Empty Gaps ("莫名的空間") (Max Deduction: 10) ---
    if symbol_bboxes:
        all_xmin = min(box.xmin for ref, box in symbol_bboxes)
        all_xmax = max(box.xmax for ref, box in symbol_bboxes)
        all_ymin = min(box.ymin for ref, box in symbol_bboxes)
        all_ymax = max(box.ymax for ref, box in symbol_bboxes)
        
        for sheet in sheets:
            tx, ty, sw, sh = 0.0, 0.0, 0.0, 0.0
            for sub in sheet[1:]:
                if isinstance(sub, list):
                    if sub[0] == 'at':
                        tx, ty = float(sub[1]), float(sub[2])
                    elif sub[0] == 'size':
                        sw, sh = float(sub[1]), float(sub[2])
            all_xmin = min(all_xmin, tx)
            all_xmax = max(all_xmax, tx + sw)
            all_ymin = min(all_ymin, ty)
            all_ymax = max(all_ymax, ty + sh)
            
        layout_w = all_xmax - all_xmin
        layout_h = all_ymax - all_ymin
        layout_area = layout_w * layout_h
        
        symbol_area_sum = sum((box.xmax - box.xmin) * (box.ymax - box.ymin) for ref, box in symbol_bboxes)
        for sheet in sheets:
            sw, sh = 0.0, 0.0
            for sub in sheet[1:]:
                if isinstance(sub, list) and sub[0] == 'size':
                    sw, sh = float(sub[1]), float(sub[2])
            symbol_area_sum += sw * sh
            
        density = symbol_area_sum / layout_area if layout_area > 0 else 1.0
        
        if density < 0.03:
            deductions += 10
            issues.append(f"[SPACING] Layout is too sparse (density: {density*100:.2f}%). W = {layout_w:.1f}mm, H = {layout_h:.1f}mm. Wasted space detected.")
        elif density < 0.06:
            deductions += 5
            issues.append(f"[SPACING] Layout is somewhat sparse (density: {density*100:.2f}%). Components could be placed closer together.")
        elif density > 0.40:
            deductions += 5
            issues.append(f"[SPACING] Layout is very crowded (density: {density*100:.2f}%). Components might be overlapping or hard to route.")

    # --- CHECK 5: Wire Path Complexity (Max Deduction: 10) ---
    complex_wires_count = 0
    for w in wire_paths:
        segments = len(w) - 1
        if segments > 3:
            complex_wires_count += 1
            if complex_wires_count <= 5:
                issues.append(f"[WIRE] Wire with too many bends ({segments} segments) starting at {w[0]}")

    if complex_wires_count > 0:
        deductions += min(complex_wires_count * 2, 10)

    # --- CHECK 6 & 7: Net Shorts and Dangling Wires (FATAL) ---
    # These are correctness failures, not aesthetics: they make the netlist wrong.
    # Any occurrence forces the score to a failing band so an AI caller knows the
    # generated schematic is unusable and must be regenerated.
    def gk(x, y):
        return (int(round(x / 1.27)), int(round(y / 1.27)))

    # Flatten wire paths into individual grid segments.
    gsegs = []
    for path in wire_paths:
        for i in range(len(path) - 1):
            gsegs.append((gk(*path[i]), gk(*path[i + 1])))

    # Physical net model: union endpoints connected by an explicit wire segment ONLY.
    # Collinear overlap and pin pass-through are deliberately NOT unioned here, so that
    # they surface as accidental merges between two otherwise-distinct nets.
    uf = {}
    def _find(n):
        uf.setdefault(n, n)
        while uf[n] != n:
            uf[n] = uf[uf[n]]
            n = uf[n]
        return n
    def _union(a, b):
        uf[_find(a)] = _find(b)
    for a, b in gsegs:
        _union(a, b)

    def _collinear_overlap(s1, s2):
        (x1, y1), (x2, y2) = s1
        (x3, y3), (x4, y4) = s2
        if x1 == x2 == x3 == x4:
            lo1, hi1 = sorted((y1, y2)); lo2, hi2 = sorted((y3, y4))
            return min(hi1, hi2) - max(lo1, lo2) > 0
        if y1 == y2 == y3 == y4:
            lo1, hi1 = sorted((x1, x2)); lo2, hi2 = sorted((x3, x4))
            return min(hi1, hi2) - max(lo1, lo2) > 0
        return False

    short_count = 0
    for i in range(len(gsegs)):
        for j in range(i + 1, len(gsegs)):
            if _collinear_overlap(gsegs[i], gsegs[j]) and _find(gsegs[i][0]) != _find(gsegs[j][0]):
                short_count += 1
                if short_count <= 10:
                    sx_, sy_ = gsegs[i][0]
                    issues.append(f"[SHORT] Different-net wires overlap near grid {(sx_, sy_)} — electrical short")

    # Pin pass-through short: a pin sitting in the interior of a wire of a different net,
    # with no junction there, silently merges nets in KiCad.
    pin_gks = {}
    for p in pins:
        pin_gks.setdefault(gk(p['x'], p['y']), p)
    junction_gks = set(gk(jx, jy) for jx, jy in junction_pts)
    for pgk, p in pin_gks.items():
        pin_net = _find(pgk) if pgk in uf else None
        for a, b in gsegs:
            if pgk == a or pgk == b:
                continue
            interior = (
                (a[0] == b[0] == pgk[0] and min(a[1], b[1]) < pgk[1] < max(a[1], b[1])) or
                (a[1] == b[1] == pgk[1] and min(a[0], b[0]) < pgk[0] < max(a[0], b[0]))
            )
            # Only a short if the pin ALSO belongs to a different net (via wires ending on
            # it). A pin whose sole connection is this pass-through wire is legitimately on
            # that wire's net — KiCad connects a pin lying on a wire without a junction.
            if interior and pgk not in junction_gks and pin_net is not None and pin_net != _find(a):
                short_count += 1
                if short_count <= 10:
                    issues.append(f"[SHORT] Pin {p['number']} of {p['ref']} lies mid-wire of another net — net merge")
                break

    # Dangling wire endpoints: a wire end touching no pin, label, junction, sheet pin,
    # or another wire (endpoint or interior).
    connector_gks = set(pin_gks.keys()) | junction_gks
    for l in labels:
        at_node = next((sub for sub in l[1:] if isinstance(sub, list) and sub[0] == 'at'), None)
        if at_node:
            connector_gks.add(gk(float(at_node[1]), float(at_node[2])))

    dangling_count = 0
    seen_dangling = set()
    for idx, (a, b) in enumerate(gsegs):
        for pt in (a, b):
            if pt in connector_gks or pt in seen_dangling:
                continue
            touched = False
            for jdx, (c, d) in enumerate(gsegs):
                if jdx == idx:
                    continue
                if pt == c or pt == d:
                    touched = True
                    break
                if (c[0] == d[0] == pt[0] and min(c[1], d[1]) <= pt[1] <= max(c[1], d[1])) or \
                   (c[1] == d[1] == pt[1] and min(c[0], d[0]) <= pt[0] <= max(c[0], d[0])):
                    touched = True
                    break
            if not touched:
                dangling_count += 1
                seen_dangling.add(pt)
                if dangling_count <= 10:
                    issues.append(f"[DANGLING] Wire end at grid {pt} connects to nothing")

    # --- CHECK 8: Duplicate wire segments (redundant "extra" wires) ---
    # Two wires with the same endpoints are visually messy ("線多") even though they are
    # electrically harmless, so they must keep the score below 100.
    seg_seen = {}
    duplicate_wires = 0
    for (a, b) in gsegs:
        if a == b:
            continue
        key = frozenset((a, b))
        seg_seen[key] = seg_seen.get(key, 0) + 1
        if seg_seen[key] >= 2:
            duplicate_wires += 1
            if duplicate_wires <= 10:
                issues.append(f"[DUPLICATE] Redundant overlapping wire segment at {tuple(sorted(key))}")

    if short_count > 0:
        deductions += min(short_count * 15, 100)
    if dangling_count > 0:
        deductions += min(dangling_count * 10, 100)
    if duplicate_wires > 0:
        deductions += min(duplicate_wires * 5, 20)

    # --- CHECK 9: Polarized Capacitor Polarity ---
    # Build a second union-find (separate from the short-check one) that includes label
    # positions so we can trace which net name reaches each pin.
    import re as _re

    def _net_parse_voltage(name):
        if name in ('GND', 'GNDA', 'GNDPWR', 'GNDD', '0V'):
            return 0.0
        # "3.3V" / "+3.3V" / "-12.5V"
        m = _re.match(r'^([+-])?(\d+)\.(\d+)[Vv]$', name)
        if m:
            sign = -1.0 if m.group(1) == '-' else 1.0
            return sign * float(f"{m.group(2)}.{m.group(3)}")
        # "+3V3" / "5V" / "-12V"
        m = _re.match(r'^([+-])?(\d+)[Vv](\d+)?$', name)
        if m:
            sign = -1.0 if m.group(1) == '-' else 1.0
            frac = int(m.group(3)) / (10 ** len(m.group(3))) if m.group(3) else 0.0
            return sign * (int(m.group(2)) + frac)
        return None

    _nuf = {}
    def _nfind(n):
        _nuf.setdefault(n, n)
        while _nuf[n] != n:
            _nuf[n] = _nuf[_nuf[n]]
            n = _nuf[n]
        return n
    def _nunion(a, b):
        _nuf[_nfind(a)] = _nfind(b)

    for path in wire_paths:
        pts_gk = [gk(*pt) for pt in path]
        for i in range(len(pts_gk) - 1):
            _nunion(pts_gk[i], pts_gk[i + 1])

    _net_names = {}  # root gk → net name
    for _l in labels:
        _lname = _l[1] if len(_l) > 1 and isinstance(_l[1], str) else None
        if not _lname:
            continue
        _at = next((s for s in _l[1:] if isinstance(s, list) and s[0] == 'at'), None)
        if _at:
            _net_names.setdefault(_nfind(gk(float(_at[1]), float(_at[2]))), _lname)
    for _sym in symbols:
        _lid = next((s[1] for s in _sym[1:] if isinstance(s, list) and s[0] == 'lib_id' and len(s) > 1), '')
        if not _lid.startswith('power:'):
            continue
        _at = next((s for s in _sym[1:] if isinstance(s, list) and s[0] == 'at' and len(s) > 2), None)
        if _at:
            _net_names.setdefault(_nfind(gk(float(_at[1]), float(_at[2]))), _lid.split(':')[1])

    def _pin_net_name(px, py):
        return _net_names.get(_nfind(gk(px, py)))

    polarity_errors = 0
    for _inst in symbols:
        _lid, _ref = '', ''
        for s in _inst[1:]:
            if isinstance(s, list) and len(s) > 1:
                if s[0] == 'lib_id':
                    _lid = s[1]
                elif s[0] == 'property' and len(s) > 2 and s[1] == 'Reference':
                    _ref = s[2]
        _lpart = _lid.split(':')[1] if ':' in _lid else _lid
        if 'C_Polarized' not in _lpart and 'CP_' not in _lpart:
            continue
        _d = local_definitions.get(_lid)
        if not _d and ':' in _lid:
            _ln, _sn = _lid.split(':', 1)
            _d = find_symbol_definition(_ln, _sn, lib_map, project_dir)
        _sp = get_symbol_pins_global(_inst, _d) if _d else []
        _pp = next((p for p in _sp if p['name'] == '+'), None) or next((p for p in _sp if p['number'] == '1'), None)
        _np = next((p for p in _sp if p['name'] == '-'), None) or next((p for p in _sp if p['number'] == '2'), None)
        if not _pp or not _np:
            continue
        _pnet = _pin_net_name(_pp['x'], _pp['y'])
        _nnet = _pin_net_name(_np['x'], _np['y'])
        if not _pnet or not _nnet:
            continue
        _pv = _net_parse_voltage(_pnet)
        _nv = _net_parse_voltage(_nnet)
        if _pv is None or _nv is None:
            continue
        if _pv < _nv:
            polarity_errors += 1
            issues.append(f"[WARN:POLARITY] {_ref} ({_lpart}): + pin → '{_pnet}' ({_pv}V) < − pin → '{_nnet}' ({_nv}V) — reversed polarity")

    score = max(100 - deductions, 0)

    # Hard fail: shorts/dangling wires are correctness defects. Cap into a failing band
    # so the score unambiguously signals "do not use this schematic".
    fatal = short_count > 0 or dangling_count > 0
    if fatal:
        score = min(score, 30)
        issues.append(f"[FATAL] {short_count} short(s) and {dangling_count} dangling wire(s) — schematic is electrically invalid.")

    # Informational only — wire routing complexity (NOT fatal, does not affect score band).
    try:
        from kicad_skill.wire_complexity import score_wire_complexity
        wire_complexity_total = score_wire_complexity(sch_path, table_path)["total"]
    except Exception:
        wire_complexity_total = 0.0

    return {
        "score": score,
        "fatal": fatal,
        "wire_complexity_total": wire_complexity_total,
        "grid_errors": grid_errors,
        "overlaps_count": overlaps_count,
        "unconnected_pins_count": unconnected_pins_count,
        "complex_wires_count": complex_wires_count,
        "duplicate_wires": duplicate_wires,
        "shorts": short_count,
        "dangling": dangling_count,
        "polarity_errors": polarity_errors,
        "issues": issues
    }

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 evaluate_layout.py <schematic_path> <table_path>")
        sys.exit(1)
        
    res = evaluate_schematic_layout(sys.argv[1], sys.argv[2])
    print(f"\n==========================================")
    print(f"       LAYOUT QUALITY EVALUATION REPORT   ")
    print(f"==========================================")
    print(f"FINAL SCORE: {res.get('score', 0)} / 100" + ("   [FATAL — DO NOT USE]" if res.get('fatal') else ""))
    print(f"------------------------------------------")
    print(f"Net shorts (FATAL):     {res.get('shorts', 0)}")
    print(f"Dangling wires (FATAL): {res.get('dangling', 0)}")
    print(f"Duplicate wires:        {res.get('duplicate_wires', 0)}")
    print(f"Wire complexity total: {res.get('wire_complexity_total', 0.0):.1f}")
    print(f"Off-grid elements:      {res.get('grid_errors', 0)}")
    print(f"Symbol overlaps:        {res.get('overlaps_count', 0)}")
    print(f"Disconnected pins:      {res.get('unconnected_pins_count', 0)}")
    print(f"Excessive wire bends:   {res.get('complex_wires_count', 0)}")
    print(f"------------------------------------------")
    if res.get("issues"):
        print("ISSUES LISTED:")
        for iss in res["issues"]:
            print(f"  {iss}")
    else:
        print("No layout issues found! Perfect layout!")
    print(f"==========================================\n")
