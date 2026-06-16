import os
import uuid
import math
from .parser import parse_sexpr, format_sexpr
from .schematic import (
    load_sym_lib_table,
    find_symbol_definition,
    get_symbol_local_bbox,
    get_symbol_instance_transform,
    get_instance_aabb,
    transform_pin_coordinate,
    find_orthogonal_path,
    make_wire_sexpr,
    dedupe_wire_children,
    BoundingBox
)

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            return x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        rx = self.find(x)
        ry = self.find(y)
        if rx != ry:
            self.parent[rx] = ry

def grid_key(x, y):
    return (int(round(float(x) / 1.27)), int(round(float(y) / 1.27)))

def grid_to_coord(gk):
    return (gk[0] * 1.27, gk[1] * 1.27)

def get_symbol_pins_global(inst, defn):
    pins = []
    if not defn:
        return pins

    pins_in_defn = []
    def traverse_pins(node):
        if not isinstance(node, list):
            return
        if node[0] == 'pin':
            pin_type = node[1]
            px, py = None, None
            pin_num, pin_name = None, None
            for p in node[1:]:
                if not isinstance(p, list):
                    continue
                if p[0] == 'at' and len(p) > 2:
                    px = float(p[1])
                    py = float(p[2])
                elif p[0] == 'name' and len(p) > 1:
                    pin_name = p[1]
                elif p[0] == 'number' and len(p) > 1:
                    pin_num = p[1]
            if px is not None and py is not None and pin_num is not None:
                pins_in_defn.append({
                    "number": pin_num,
                    "name": pin_name or "",
                    "type": pin_type,
                    "local_x": px,
                    "local_y": py
                })
        else:
            for child in node[1:]:
                traverse_pins(child)

    traverse_pins(defn)

    tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst)
    for p in pins_in_defn:
        gx, gy = transform_pin_coordinate(p["local_x"], p["local_y"], tx, ty, angle, mirror_x, mirror_y)
        pins.append({
            "number": p["number"],
            "name": p["name"],
            "type": p["type"],
            "x": gx,
            "y": gy
        })
    return pins

def shift_coordinates(node, dx, dy):
    if not isinstance(node, list):
        return
    if len(node) > 2 and node[0] in ('at', 'xy'):
        try:
            node[1] = f"{float(node[1]) + dx:.3f}"
            node[2] = f"{float(node[2]) + dy:.3f}"
        except ValueError:
            pass
    else:
        for child in node:
            shift_coordinates(child, dx, dy)

def compute_blocked_wire_dirs(children):
    """Build {grid_key: set((dx,dy))} blocking collinear overlap along existing wires
    (perpendicular crossings stay allowed). Mirrors connect_symbols_in_schematic."""
    bwd = {}
    for ch in children:
        if not (isinstance(ch, list) and ch and ch[0] == 'wire'):
            continue
        pts = next((s for s in ch[1:] if isinstance(s, list) and s[0] == 'pts'), None)
        if not pts:
            continue
        cs = [(float(a[1]), float(a[2])) for a in pts[1:] if isinstance(a, list) and len(a) > 2 and a[0] == 'xy']
        if len(cs) < 2:
            continue
        g1 = grid_key(*cs[0])
        g2 = grid_key(*cs[-1])
        if g1[0] == g2[0]:
            dirs = {(0, 1), (0, -1)}
            for gy in range(min(g1[1], g2[1]), max(g1[1], g2[1]) + 1):
                bwd.setdefault((g1[0], gy), set()).update(dirs)
        elif g1[1] == g2[1]:
            dirs = {(1, 0), (-1, 0)}
            for gx in range(min(g1[0], g2[0]), max(g1[0], g2[0]) + 1):
                bwd.setdefault((gx, g1[1]), set()).update(dirs)
        else:
            dirs = {(1, 0), (-1, 0), (0, 1), (0, -1)}
            bwd.setdefault(g1, set()).update(dirs)
            bwd.setdefault(g2, set()).update(dirs)
    return bwd


def prune_dangling_wires(children, connector_gks):
    """Iteratively delete wire nodes that have an endpoint connected to nothing
    (no pin/label/sheet-pin/junction and no other wire). Removes crossing-net stubs
    that were dragged into a sheet but lead to empty space."""
    def wire_segments():
        segs = []
        for ch in children:
            if not (isinstance(ch, list) and ch and ch[0] == 'wire'):
                continue
            pts = next((s for s in ch[1:] if isinstance(s, list) and s[0] == 'pts'), None)
            if not pts:
                continue
            cs = [(float(a[1]), float(a[2])) for a in pts[1:] if isinstance(a, list) and len(a) > 2 and a[0] == 'xy']
            if len(cs) >= 2:
                segs.append((ch, grid_key(*cs[0]), grid_key(*cs[-1])))
        return segs

    while True:
        segs = wire_segments()
        remove_ids = set()
        for node, a, b in segs:
            for pt in (a, b):
                if pt in connector_gks:
                    continue
                touched = False
                for n2, c, d in segs:
                    if n2 is node:
                        continue
                    if pt == c or pt == d:
                        touched = True
                        break
                    if c[0] == d[0] == pt[0] and min(c[1], d[1]) <= pt[1] <= max(c[1], d[1]):
                        touched = True
                        break
                    if c[1] == d[1] == pt[1] and min(c[0], d[0]) <= pt[0] <= max(c[0], d[0]):
                        touched = True
                        break
                if not touched:
                    remove_ids.add(id(node))
                    break
        if not remove_ids:
            return
        children[:] = [c for c in children if not (isinstance(c, list) and id(c) in remove_ids)]


# ---- Net-label placement (collision-aware) -------------------------------------
# A label anchored straight on a pin overlaps the pin name, the symbol reference /
# value, and neighbouring labels. These helpers orient the text away from the body
# and push it outward (with a stub wire back to the pin) until its AABB clears every
# obstacle, so the rendered schematic stays readable.
_LABEL_CHAR_W = 1.1    # mm per character at 1.27 mm font (KiCad glyphs ~0.85*size wide)
_LABEL_GAP = 2.54      # breathing room + the hierarchical-label flag/arrow graphic
_TEXT_H = 2.2          # label text height incl. padding


def _boxes_overlap(a, b):
    return a.xmin < b.xmax and a.xmax > b.xmin and a.ymin < b.ymax and a.ymax > b.ymin


def _label_text_box(text, x, y, angle, justify):
    """Approx AABB of a label's text box anchored at (x, y)."""
    w = max(1, len(text)) * _LABEL_CHAR_W + _LABEL_GAP
    h = _TEXT_H
    if angle == 90:
        if justify == "left":      # vertical text growing up (-y)
            return BoundingBox(x - h / 2, y - w, x + h / 2, y)
        if justify == "right":     # growing down (+y)
            return BoundingBox(x - h / 2, y, x + h / 2, y + w)
        return BoundingBox(x - h / 2, y - w / 2, x + h / 2, y + w / 2)
    if justify == "left":          # horizontal text growing right (+x)
        return BoundingBox(x, y - h / 2, x + w, y + h / 2)
    if justify == "right":         # growing left (-x)
        return BoundingBox(x - w, y - h / 2, x, y + h / 2)
    return BoundingBox(x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _wire_box(p1, p2):
    """Thin AABB around a wire segment (so labels don't land on top of it)."""
    return BoundingBox(min(p1[0], p2[0]) - 0.2, min(p1[1], p2[1]) - 0.2,
                       max(p1[0], p2[0]) + 0.2, max(p1[1], p2[1]) + 0.2)


def _property_box(inst, *names):
    """AABB(es) of a symbol instance's Reference/Value (or other) property text."""
    boxes = []
    for sub in inst[1:]:
        if (isinstance(sub, list) and sub and sub[0] == 'property'
                and len(sub) > 2 and sub[1] in names):
            at = next((q for q in sub[1:] if isinstance(q, list) and q[0] == 'at'), None)
            if at:
                boxes.append(_label_text_box(str(sub[2]), float(at[1]), float(at[2]), 0, 'left'))
    return boxes


def _symbol_obstacle_box(inst, defn, dx=0.0, dy=0.0, pad=2.54):
    """AABB of a placed symbol that INCLUDES its pins (and a text-margin pad), so
    labels are pushed clear of pin numbers / pin names — not just the body graphic.
    get_symbol_local_bbox covers only the body shapes, so pins are unioned in here."""
    local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
    tx, ty, a_i, mx_i, my_i = get_symbol_instance_transform(inst)
    box = get_instance_aabb(local_bbox, tx, ty, a_i, mx_i, my_i)
    box = BoundingBox(box.xmin, box.ymin, box.xmax, box.ymax)
    for p in get_symbol_pins_global(inst, defn):
        box.update_point(p['x'], p['y'])
    return BoundingBox(box.xmin + dx - pad, box.ymin + dy - pad,
                       box.xmax + dx + pad, box.ymax + dy + pad)


def _update_label_at(node, x, y):
    """Move a label/hier-label node's anchor, preserving its angle."""
    for i, s in enumerate(node):
        if isinstance(s, list) and s and s[0] == 'at':
            ang = s[3] if len(s) > 3 else '0'
            node[i] = ['at', f"{x:.3f}", f"{y:.3f}", ang]
            return


def _update_wire_end(wire, x, y):
    """Move a wire's SECOND endpoint (the label end of a stub)."""
    pts = next((s for s in wire[1:] if isinstance(s, list) and s and s[0] == 'pts'), None)
    if pts and len(pts) >= 3:
        pts[2] = ['xy', f"{x:.3f}", f"{y:.3f}"]


def _seg_hits_point(ax, ay, bx, by, px, py, eps=0.06):
    """True if point (px,py) lies on the axis-aligned segment (ax,ay)-(bx,by)."""
    if abs(ax - bx) <= eps:
        return abs(px - ax) <= eps and min(ay, by) - eps <= py <= max(ay, by) + eps
    if abs(ay - by) <= eps:
        return abs(py - ay) <= eps and min(ax, bx) - eps <= px <= max(ax, bx) + eps
    return False


def _stub_bad(sx, sy, lx, ly, cross_boxes, recs, skip=None):
    """True if the stub wire (sx,sy)->(lx,ly) is illegal: it passes through a cross
    body (padded, includes pins → guards against shorting another symbol's pin) OR it
    passes over another label's anchor (a wire crossing a label connects to it)."""
    wb = _wire_box((sx, sy), (lx, ly))
    if any(_boxes_overlap(wb, c) for c in cross_boxes):
        return True
    for o in recs:
        if o is skip:
            continue
        if _seg_hits_point(sx, sy, lx, ly, o['lx'], o['ly']):
            return True
    return False


def _place_label(children, recs, base_obstacles, vfan, name, px, py, cx, cy, kind, shape,
                 cross_boxes):
    """Place one net label off pin (px,py) of a symbol/sheet centred at (cx,cy).

    Left/right pins get a short straight stub. Top/bottom pins get an L-stub (staggered
    vertical riser + horizontal run); the run direction is chosen by ray clearance so it
    fans toward open space rather than through a neighbouring symbol. The label text is
    pushed a short, bounded distance clear of bodies/text/other labels; stubs are kept
    short so they never reach another symbol's pin. `cross_boxes` = bodies the stub must
    not cross. Nodes appended to `children`; a record appended to `recs`."""
    ddx, ddy = px - cx, py - cy
    riser = None
    text_obstacles = base_obstacles + [r['box'] for r in recs]

    def push(lx, ly, ux, uy, just, cap=6):
        box = _label_text_box(name, lx, ly, 0, just)
        for _ in range(cap):
            if not any(_boxes_overlap(box, o) for o in text_obstacles):
                break
            nlx, nly = lx + ux * 1.27, ly + uy * 1.27
            if _stub_bad(sx, sy, nlx, nly, cross_boxes, recs):
                break                       # never push a stub through a body/pin/label
            lx, ly = nlx, nly
            box = _label_text_box(name, lx, ly, 0, just)
        return lx, ly, box

    if abs(ddx) >= abs(ddy):
        ux = 1.0 if ddx >= 0 else -1.0
        just = 'left' if ux > 0 else 'right'
        sx, sy = px, py
        lx, ly, box = push(px + ux * 2.54, py, ux, 0.0, just)
        hl_ang, rux, ruy = (0 if ux > 0 else 180), ux, 0.0
    else:
        uy = 1.0 if ddy >= 0 else -1.0
        key = (round(cx, 1), round(cy, 1), uy)
        idx = vfan.get(key, 0)
        vfan[key] = idx + 1
        kx, ky = px, py + uy * (3.81 + idx * 2.54)
        sx, sy = kx, ky
        # Fan toward whichever horizontal side is clear of other symbols (ray test).
        clear = [hx for hx in (1.0, -1.0)
                 if not _stub_bad(kx, ky, kx + hx * 11.0, ky, cross_boxes, recs)]
        hx = clear[0] if clear else 1.0
        just = 'left' if hx > 0 else 'right'
        lx, ly, box = push(kx + hx * 2.54, ky, hx, 0.0, just)
        riser = make_wire_sexpr(px, py, kx, ky)
        base_obstacles.append(_wire_box((px, py), (kx, ky)))
        hl_ang, rux, ruy = (0 if hx > 0 else 180), hx, 0.0
    wire = make_wire_sexpr(sx, sy, lx, ly)
    node = [kind, name]
    if shape is not None:
        node.append(['shape', shape])
    node += [['at', f"{lx:.3f}", f"{ly:.3f}", str(hl_ang)],
             ['effects', ['font', ['size', '1.27', '1.27']], ['justify', just]],
             ['uuid', str(uuid.uuid4())]]
    if riser is not None:
        children.append(riser)
    children.append(wire)
    children.append(node)
    recs.append({'name': name, 'spx': sx, 'spy': sy, 'ux': rux, 'uy': ruy,
                 'bx_just': just, 'lx': lx, 'ly': ly, 'box': box, 'cross': cross_boxes,
                 'label_node': node, 'wire_node': wire})


def _reconcile_labels(records, base_obstacles, max_passes=6):
    """Iteratively push any label whose text box overlaps a symbol/text obstacle,
    another label, or another stub wire further along its run axis — but NEVER past the
    point where its stub would cross a body/pin (that would short nets). Mutates each
    record's label + stub in place."""
    for _ in range(max_passes):
        moved = False
        for r in records:
            text_obs = list(base_obstacles)
            for o in records:
                if o is r:
                    continue
                text_obs.append(o['box'])
                text_obs.append(_wire_box((o['spx'], o['spy']), (o['lx'], o['ly'])))
            steps = 0
            while steps < 6 and any(_boxes_overlap(r['box'], o) for o in text_obs):
                nlx, nly = r['lx'] + r['ux'] * 1.27, r['ly'] + r['uy'] * 1.27
                if _stub_bad(r['spx'], r['spy'], nlx, nly, r['cross'], records, skip=r):
                    break
                r['lx'], r['ly'] = nlx, nly
                r['box'] = _label_text_box(r['name'], r['lx'], r['ly'], 0, r['bx_just'])
                steps += 1
            if steps:
                moved = True
                _update_label_at(r['label_node'], r['lx'], r['ly'])
                _update_wire_end(r['wire_node'], r['lx'], r['ly'])
        if not moved:
            break


def _set_symbol_instance_path(inst, project_name, path, reference):
    """Replace a symbol instance's `instances` block so its hierarchical path is
    correct for the sheet it now lives on. For a symbol moved into a sub-sheet the
    path is `/<root_uuid>/<sheet_instance_uuid>`; without it KiCad does not place
    the symbol on the sheet instance and ERC reports its pins unconnected."""
    inst[:] = [s for s in inst if not (isinstance(s, list) and s and s[0] == 'instances')]
    inst.append(['instances',
                 ['project', project_name,
                  ['path', path, ['reference', reference], ['unit', '1']]]])


def create_module_from_components(schematic_path, table_path, components, module_name, sheet_file_name):
    project_dir = os.path.dirname(os.path.abspath(schematic_path))
    lib_map = load_sym_lib_table(table_path)

    # 1. Read parent schematic
    with open(schematic_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)

    if not sch_sexpr or sch_sexpr[0] != 'kicad_sch':
        raise ValueError(f"Invalid KiCad schematic file {schematic_path}")

    # Root sheet uuid + project name + the new sheet instance uuid drive the
    # hierarchical instance paths of everything moved into the sub-sheet.
    root_uuid = next((c[1] for c in sch_sexpr[1:]
                      if isinstance(c, list) and c and c[0] == 'uuid'), None)
    if root_uuid is None:
        root_uuid = str(uuid.uuid4())
        sch_sexpr.insert(1, ['uuid', root_uuid])
    project_name = os.path.splitext(os.path.basename(schematic_path))[0]
    sheet_uuid = str(uuid.uuid4())
    sub_instance_path = f"/{root_uuid}/{sheet_uuid}"

    # Parse symbol definitions
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

    # 2. Identify moved and outside symbol instances
    instances_by_ref = {}
    moved_instances = {}
    outside_instances = {}
    moved_refs = set(components)

    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            lib_id_val = None
            ref_val = None
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == 'lib_id':
                        lib_id_val = sub[1]
                    elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                        ref_val = sub[2]
            if ref_val:
                inst_data = {
                    'sexpr': child,
                    'lib_id': lib_id_val,
                    'ref': ref_val
                }
                instances_by_ref[ref_val] = inst_data
                if ref_val in moved_refs:
                    moved_instances[ref_val] = inst_data
                else:
                    outside_instances[ref_val] = inst_data

    # Ensure all requested components are found
    missing_refs = moved_refs - set(instances_by_ref.keys())
    if missing_refs:
        raise ValueError(f"The following components were not found in the schematic: {', '.join(missing_refs)}")

    # Ensure symbol definitions are loaded
    for ref, inst in moved_instances.items():
        lib_id = inst['lib_id']
        if lib_id not in local_definitions and ':' in lib_id:
            lib_name, sym_name = lib_id.split(':', 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            if defn:
                defn[1] = lib_id
                local_definitions[lib_id] = defn
                if lib_symbols:
                    lib_symbols.append(defn)

    # 3. Calculate bounding box and center of moved components
    moved_boxes = []
    for ref, inst in moved_instances.items():
        defn = local_definitions.get(inst['lib_id'])
        local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst['sexpr'])
        global_bbox = get_instance_aabb(local_bbox, tx, ty, angle, mirror_x, mirror_y)
        moved_boxes.append(global_bbox)

    if not moved_boxes:
        raise ValueError("No components to group.")

    min_x = min(box.xmin for box in moved_boxes)
    max_x = max(box.xmax for box in moved_boxes)
    min_y = min(box.ymin for box in moved_boxes)
    max_y = max(box.ymax for box in moved_boxes)

    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    cx = round(cx / 1.27) * 1.27
    cy = round(cy / 1.27) * 1.27

    # Center offsets for moving elements into sub-schematic (centered around A4 middle)
    target_cx = round(150.0 / 1.27) * 1.27
    target_cy = round(100.0 / 1.27) * 1.27
    dx = target_cx - cx
    dy = target_cy - cy

    # 4. Build connectivity graph
    uf = UnionFind()
    coord_to_pins = {}
    ref_to_pins = {}

    for ref, inst in instances_by_ref.items():
        defn = local_definitions.get(inst['lib_id'])
        pins = get_symbol_pins_global(inst['sexpr'], defn)
        ref_to_pins[ref] = pins
        for p in pins:
            gk = grid_key(p['x'], p['y'])
            uf.union(gk, gk)
            coord_to_pins.setdefault(gk, []).append({
                'ref': ref,
                'number': p['number'],
                'name': p['name'],
                'type': p['type'],
                'x': p['x'],
                'y': p['y']
            })

    # Collect wires, junctions, and labels
    wires = []
    junctions = []
    labels = []
    global_labels = []
    hierarchical_labels = []

    for child in sch_sexpr[1:]:
        if not isinstance(child, list) or len(child) == 0:
            continue
        tag = child[0]
        if tag == 'wire':
            pts_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'pts':
                    pts_node = sub
                    break
            if pts_node:
                x1, y1 = None, None
                x2, y2 = None, None
                for xy in pts_node[1:]:
                    if isinstance(xy, list) and len(xy) > 2 and xy[0] == 'xy':
                        if x1 is None:
                            x1 = float(xy[1])
                            y1 = float(xy[2])
                        else:
                            x2 = float(xy[1])
                            y2 = float(xy[2])
                if x1 is not None and x2 is not None:
                    wires.append((child, (x1, y1), (x2, y2)))
                    uf.union(grid_key(x1, y1), grid_key(x2, y2))
        elif tag == 'junction':
            at_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'at':
                    at_node = sub
                    break
            if at_node:
                jx = float(at_node[1])
                jy = float(at_node[2])
                junctions.append((child, (jx, jy)))
                uf.union(grid_key(jx, jy), grid_key(jx, jy))
        elif tag == 'label':
            at_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'at':
                    at_node = sub
                    break
            if at_node:
                lx = float(at_node[1])
                ly = float(at_node[2])
                labels.append((child, child[1], (lx, ly)))
                uf.union(grid_key(lx, ly), grid_key(lx, ly))
        elif tag == 'global_label':
            at_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'at':
                    at_node = sub
                    break
            if at_node:
                glx = float(at_node[1])
                gly = float(at_node[2])
                global_labels.append((child, child[1], (glx, gly)))
                uf.union(grid_key(glx, gly), grid_key(glx, gly))
        elif tag == 'hierarchical_label':
            at_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'at':
                    at_node = sub
                    break
            if at_node:
                hlx = float(at_node[1])
                hly = float(at_node[2])
                hierarchical_labels.append((child, child[1], (hlx, hly)))
                uf.union(grid_key(hlx, hly), grid_key(hlx, hly))

    # Union identical local labels and global labels
    label_to_coords = {}
    for node, text, pos in labels:
        label_to_coords.setdefault(text, []).append(pos)
    for text, coords in label_to_coords.items():
        if len(coords) > 1:
            gk0 = grid_key(coords[0][0], coords[0][1])
            for pos in coords[1:]:
                gk = grid_key(pos[0], pos[1])
                uf.union(gk0, gk)

    global_label_to_coords = {}
    for node, text, pos in global_labels:
        global_label_to_coords.setdefault(text, []).append(pos)
    for text, coords in global_label_to_coords.items():
        if len(coords) > 1:
            gk0 = grid_key(coords[0][0], coords[0][1])
            for pos in coords[1:]:
                gk = grid_key(pos[0], pos[1])
                uf.union(gk0, gk)

    # Group by net root
    nets = {}
    for element in uf.parent:
        root = uf.find(element)
        nets.setdefault(root, {
            'coords': set(),
            'pins': [],
            'labels': [],
            'global_labels': [],
            'hierarchical_labels': []
        })
        nets[root]['coords'].add(element)

    for gk, pins in coord_to_pins.items():
        root = uf.find(gk)
        if root in nets:
            nets[root]['pins'].extend(pins)

    for node, text, pos in labels:
        root = uf.find(grid_key(pos[0], pos[1]))
        if root in nets:
            nets[root]['labels'].append((node, text, pos))

    for node, text, pos in global_labels:
        root = uf.find(grid_key(pos[0], pos[1]))
        if root in nets:
            nets[root]['global_labels'].append((node, text, pos))

    for node, text, pos in hierarchical_labels:
        root = uf.find(grid_key(pos[0], pos[1]))
        if root in nets:
            nets[root]['hierarchical_labels'].append((node, text, pos))

    # 5. Classify Nets and Identify Boundary-Crossing Nets
    boundary_nets = {}
    for root, N in nets.items():
        inside_pins = [p for p in N['pins'] if p['ref'] in moved_refs]
        outside_pins = [p for p in N['pins'] if p['ref'] not in moved_refs]
        has_inside = len(inside_pins) > 0
        has_outside = len(outside_pins) > 0

        # Check for shared local labels as boundary crossings
        has_shared_label = False
        inside_labels = [l[1] for l in N['labels'] if grid_key(l[2][0], l[2][1]) in N['coords']]
        if inside_labels:
            # Check if this label name is used by any outside component
            for other_root, other_N in nets.items():
                if other_root == root:
                    continue
                other_outside_pins = [p for p in other_N['pins'] if p['ref'] not in moved_refs]
                if other_outside_pins:
                    other_labels = [l[1] for l in other_N['labels']]
                    if any(lbl in inside_labels for lbl in other_labels):
                        has_shared_label = True
                        break

        # A net crosses boundary if it connects inside components to outside components (via pins or labels)
        if has_inside and (has_outside or has_shared_label):
            # Determine Name
            net_name = None
            # Prioritize hierarchical label, then local label, then global label
            if N['hierarchical_labels']:
                net_name = N['hierarchical_labels'][0][1]
            elif N['labels']:
                net_name = N['labels'][0][1]
            elif N['global_labels']:
                net_name = N['global_labels'][0][1]
            else:
                # Generate unique net name based on inside pin
                ref_pin = inside_pins[0]
                pin_name_clean = ref_pin['name'].replace('/', '_').replace(' ', '_')
                net_name = f"{ref_pin['ref']}_{pin_name_clean or ref_pin['number']}"

            # Determine type
            in_types = [p['type'] for p in inside_pins]
            if all(t == 'input' for t in in_types):
                pin_type = 'input'
            elif all(t == 'output' for t in in_types):
                pin_type = 'output'
            elif any(t in ('bidirectional', 'tri_state') for t in in_types):
                pin_type = 'bidirectional'
            else:
                pin_type = 'passive'

            boundary_nets[root] = {
                'name': net_name,
                'type': pin_type,
                'inside_pins': inside_pins,
                'outside_pins': outside_pins,
                'coords': N['coords']
            }

    # 6. Run BFS to separate wires, junctions, and labels
    inside_coords_all = set()
    inside_wire_ids = set()
    crossing_wires = []

    for root, N in nets.items():
        inside_pins = [p for p in N['pins'] if p['ref'] in moved_refs]
        if not inside_pins:
            continue

        # Build adjacency list for this net
        adj = {}
        wire_id_to_node = {}
        for wire_node, p1, p2 in wires:
            gk1 = grid_key(p1[0], p1[1])
            gk2 = grid_key(p2[0], p2[1])
            if uf.find(gk1) == root:
                adj.setdefault(gk1, set()).add((gk2, id(wire_node)))
                adj.setdefault(gk2, set()).add((gk1, id(wire_node)))
                wire_id_to_node[id(wire_node)] = wire_node

        # BFS starting from inside pins
        inside_coords = set(grid_key(p['x'], p['y']) for p in inside_pins)
        queue = list(inside_coords)
        visited = set(inside_coords)

        while queue:
            curr = queue.pop(0)
            for neighbor, wire_id in adj.get(curr, []):
                # Stop if neighbor is an outside pin
                is_outside_pin = any(p['ref'] not in moved_refs for p in coord_to_pins.get(neighbor, []))
                if is_outside_pin:
                    crossing_wires.append(wire_id_to_node[wire_id])
                    continue
                if neighbor not in visited:
                    visited.add(neighbor)
                    inside_wire_ids.add(wire_id)
                    queue.append(neighbor)
                else:
                    inside_wire_ids.add(wire_id)

        inside_coords_all.update(visited)

    # Filter which elements to move into the sub-sheet. Only fully-internal nets
    # (every pin inside the module) keep their original regen wiring verbatim — it
    # is already ERC-clean. Boundary-net wires are NOT moved: cutting a multi-segment
    # crossing chain leaves inside stub remnants whose endpoints land mid-span on
    # other nets' wires, and KiCad treats a wire-endpoint-on-wire as a junction —
    # bridging different nets (e.g. VDD<->GND) into one short. Boundary nets are
    # rebuilt cleanly below (interconnect inside pins + one hierarchical label).
    boundary_roots = set(boundary_nets.keys())

    def _wire_root(w):
        return uf.find(grid_key(w[1][0], w[1][1]))

    moved_wires_nodes = [w[0] for w in wires
                         if id(w[0]) in inside_wire_ids and _wire_root(w) not in boundary_roots]
    moved_junctions_nodes = [j[0] for j in junctions
                             if grid_key(j[1][0], j[1][1]) in inside_coords_all
                             and uf.find(grid_key(j[1][0], j[1][1])) not in boundary_roots]
    moved_labels_nodes = [l[0] for l in labels
                          if grid_key(l[2][0], l[2][1]) in inside_coords_all
                          and uf.find(grid_key(l[2][0], l[2][1])) not in boundary_roots]
    moved_global_labels_nodes = [gl[0] for gl in global_labels
                                 if grid_key(gl[2][0], gl[2][1]) in inside_coords_all
                                 and uf.find(grid_key(gl[2][0], gl[2][1])) not in boundary_roots]

    # Delete crossing wires from parent schematic
    crossing_wire_ids = set(id(w) for w in crossing_wires)

    # 7. Create Sub-Schematic File
    sub_sch_path = os.path.join(project_dir, sheet_file_name)
    sub_uuid = str(uuid.uuid4())

    sub_sch_children = [
        'kicad_sch',
        ['version', sch_sexpr[1][1] if len(sch_sexpr) > 1 and sch_sexpr[1][0] == 'version' else '20211123'],
        ['generator', 'eeschema'],
        ['generator_version', '10.0'],
        ['uuid', sub_uuid],
        ['paper', 'A4']
    ]

    # Add library symbols
    sub_lib_symbols = ['lib_symbols']
    used_lib_ids = set(inst['lib_id'] for inst in moved_instances.values())
    for lib_id in used_lib_ids:
        defn = local_definitions.get(lib_id)
        if defn:
            sub_lib_symbols.append(defn)
    sub_sch_children.append(sub_lib_symbols)

    # Move symbol instances to sub-schematic and shift them
    for ref, inst in moved_instances.items():
        inst_copy = parse_sexpr(format_sexpr(inst['sexpr'])) # deep copy
        shift_coordinates(inst_copy, dx, dy)
        _set_symbol_instance_path(inst_copy, project_name, sub_instance_path, ref)
        sub_sch_children.append(inst_copy)

    # NOTE: the original regen wires/junctions/labels are intentionally NOT moved
    # into the sub-sheet. All intra-module connectivity is rebuilt below purely with
    # labels (local for fully-internal nets, hierarchical for boundary nets). A label
    # connects every same-named label by name, with no geometry — so the sub-sheet has
    # zero wires and therefore zero chance of a stray wire crossing / T-junction short.

    # Fixed obstacles for label placement: symbol bodies (incl. pins + text margin)
    # and reference/value text. Labels + stub wires are reconciled against each other
    # in a second pass after all are placed (see _reconcile_labels).
    sub_base_obstacles = []
    sub_label_recs = []
    sub_symbol_boxes = {}     # ref -> body box (stub wires must not cross OTHER symbols)
    moved_centers = {}
    for ref, inst in moved_instances.items():
        defn = local_definitions.get(inst['lib_id'])
        local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, angle_i, mx_i, my_i = get_symbol_instance_transform(inst['sexpr'])
        body = get_instance_aabb(local_bbox, tx, ty, angle_i, mx_i, my_i)
        # outward direction uses the body-graphic centre (pin-exclusive, so pins on
        # one side don't bias it); the obstacle box includes pins + a text margin.
        moved_centers[ref] = (body.center[0] + dx, body.center[1] + dy)
        obox = _symbol_obstacle_box(inst['sexpr'], defn, dx, dy)
        sub_symbol_boxes[ref] = obox
        sub_base_obstacles.append(obox)
        for b in _property_box(inst['sexpr'], 'Reference', 'Value'):
            sub_base_obstacles.append(BoundingBox(b.xmin + dx, b.ymin + dy,
                                                  b.xmax + dx, b.ymax + dy))

    vfan = {}   # (cx,cy,sign(uy)) -> count, to stagger top/bottom-pin riser heights

    def _emit_sub_label(name, pin, kind, shape):
        spx, spy = pin['x'] + dx, pin['y'] + dy
        ocx, ocy = moved_centers.get(pin['ref'], (spx, spy))
        cross = [b for r, b in sub_symbol_boxes.items() if r != pin['ref']]
        _place_label(sub_sch_children, sub_label_recs, sub_base_obstacles, vfan,
                     name, spx, spy, ocx, ocy, kind, shape, cross)

    def _unique_inside_pins(pin_list):
        seen, out = set(), []
        for p in pin_list:
            gk = grid_key(p['x'] + dx, p['y'] + dy)
            if gk not in seen:
                seen.add(gk)
                out.append(p)
        return out

    # Boundary nets -> one hierarchical label per inside pin (same name = one net =
    # one parent sheet pin). The sheet-pin side follows where the outside pins sit.
    net_to_pin_coords = {}
    for root, net_info in boundary_nets.items():
        outside_coords = [grid_to_coord(gk) for gk in net_info['coords'] if gk not in inside_coords_all]
        avg_out_x = sum(c[0] for c in outside_coords) / len(outside_coords) if outside_coords else cx
        net_to_pin_coords[root] = {'side': 'left' if avg_out_x < cx else 'right'}
        for p in _unique_inside_pins(net_info['inside_pins']):
            _emit_sub_label(net_info['name'], p, 'hierarchical_label', net_info['type'])

    # Fully-internal nets (every pin inside the module) -> one local label per pin.
    # Local labels connect by name with no geometry, so a multi-pin internal net needs
    # no routed wires — keeping the sub-sheet free of any net-crossing wires.
    for root, N in nets.items():
        if root in boundary_nets:
            continue
        inside_pins = _unique_inside_pins([p for p in N['pins'] if p['ref'] in moved_refs])
        if len(inside_pins) < 2:
            continue   # 0/1-pin net: nothing to interconnect
        if N['labels']:
            name = N['labels'][0][1]
        elif N['global_labels']:
            name = N['global_labels'][0][1]
        else:
            rp = inside_pins[0]
            clean = (rp['name'] or rp['number']).replace('/', '_').replace(' ', '_')
            name = f"{rp['ref']}_{clean}"
        for p in inside_pins:
            _emit_sub_label(name, p, 'label', None)

    # Second pass: settle any label whose text now overlaps another label or a stub
    # wire that was added after it (the cause of labels appearing to sit on a wire).
    _reconcile_labels(sub_label_recs, sub_base_obstacles)

    # Prune crossing-net wire stubs dragged into the sub-sheet (dangling to empty space)
    sub_connector_gks = set()
    for ch in sub_sch_children:
        if not isinstance(ch, list) or not ch:
            continue
        if ch[0] == 'symbol':
            lib_id = next((s[1] for s in ch[1:] if isinstance(s, list) and len(s) > 1 and s[0] == 'lib_id'), None)
            defn = local_definitions.get(lib_id)
            for p in get_symbol_pins_global(ch, defn):
                sub_connector_gks.add(grid_key(p['x'], p['y']))
        elif ch[0] in ('hierarchical_label', 'label', 'global_label', 'junction'):
            at = next((s for s in ch[1:] if isinstance(s, list) and s[0] == 'at'), None)
            if at:
                sub_connector_gks.add(grid_key(float(at[1]), float(at[2])))
    prune_dangling_wires(sub_sch_children, sub_connector_gks)
    sub_sch_children = [sub_sch_children[0]] + dedupe_wire_children(sub_sch_children[1:])

    # Save child schematic
    with open(sub_sch_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sub_sch_children))

    # 8. Update Parent Schematic
    # Remove moved symbols plus every wire/junction/label of a boundary net. The
    # parent side of each boundary net is reconnected purely by local labels (below)
    # named after the net, so all original boundary-net geometry is dropped: leaving
    # it risks dangling stubs and T-junction bridges (a re-routed corner landing
    # mid-span on another net's wire shorts them). Fully-outside nets are untouched.
    moved_wire_ids = set(id(w) for w in moved_wires_nodes)
    moved_j_ids = set(id(j) for j in moved_junctions_nodes)
    moved_l_ids = set(id(l) for l in moved_labels_nodes)
    moved_inst_ids = set(id(inst['sexpr']) for inst in moved_instances.values())

    boundary_wire_ids = set(id(w[0]) for w in wires
                            if uf.find(grid_key(w[1][0], w[1][1])) in boundary_roots)
    boundary_j_ids = set(id(j[0]) for j in junctions
                         if uf.find(grid_key(j[1][0], j[1][1])) in boundary_roots)
    boundary_l_ids = set(id(l[0]) for l in labels
                         if uf.find(grid_key(l[2][0], l[2][1])) in boundary_roots)

    parent_children = []
    for child in sch_sexpr[1:]:
        if not isinstance(child, list) or len(child) == 0:
            parent_children.append(child)
            continue
        if child[0] == 'symbol' and id(child) in moved_inst_ids:
            continue
        if child[0] == 'wire' and (id(child) in moved_wire_ids
                                   or id(child) in crossing_wire_ids
                                   or id(child) in boundary_wire_ids):
            continue
        if child[0] == 'junction' and (id(child) in moved_j_ids or id(child) in boundary_j_ids):
            continue
        if child[0] == 'label' and (id(child) in moved_l_ids or id(child) in boundary_l_ids):
            continue
        parent_children.append(child)

    # Define sheet size and placement
    left_side_nets = [r for r, info in net_to_pin_coords.items() if info['side'] == 'left']
    right_side_nets = [r for r, info in net_to_pin_coords.items() if info['side'] == 'right']

    pin_spacing = 5.08
    sheet_w = 38.1
    sheet_h = max(max(len(left_side_nets), len(right_side_nets)), 1) * pin_spacing + 5.08

    # Sheet position centered where the old components were
    sheet_x = round((cx - sheet_w / 2) / 1.27) * 1.27
    sheet_y = round((cy - sheet_h / 2) / 1.27) * 1.27

    # Create sheet pins S-expression
    sheet_pins_sexpr = []
    
    # Left edge pins (pointing left)
    for idx, root in enumerate(left_side_nets):
        py = sheet_y + pin_spacing + idx * pin_spacing
        px = sheet_x
        pin_uuid = str(uuid.uuid4())
        net_to_pin_coords[root]['parent_pin_x'] = px
        net_to_pin_coords[root]['parent_pin_y'] = py
        
        pin_expr = [
            'pin', boundary_nets[root]['name'], boundary_nets[root]['type'],
            ['at', f"{px:.3f}", f"{py:.3f}", "180"],
            ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'left']],
            ['uuid', pin_uuid]
        ]
        sheet_pins_sexpr.append(pin_expr)

    # Right edge pins (pointing right)
    for idx, root in enumerate(right_side_nets):
        py = sheet_y + pin_spacing + idx * pin_spacing
        px = sheet_x + sheet_w
        pin_uuid = str(uuid.uuid4())
        net_to_pin_coords[root]['parent_pin_x'] = px
        net_to_pin_coords[root]['parent_pin_y'] = py

        pin_expr = [
            'pin', boundary_nets[root]['name'], boundary_nets[root]['type'],
            ['at', f"{px:.3f}", f"{py:.3f}", "0"],
            ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'right']],
            ['uuid', pin_uuid]
        ]
        sheet_pins_sexpr.append(pin_expr)

    # Construct the sheet symbol
    sheet_node = [
        'sheet',
        ['at', f"{sheet_x:.3f}", f"{sheet_y:.3f}"],
        ['size', f"{sheet_w:.3f}", f"{sheet_h:.3f}"],
        ['exclude_from_sim', 'no'],
        ['in_bom', 'yes'],
        ['on_board', 'yes'],
        ['dnp', 'no'],
        ['fields_autoplaced', 'yes'],
        ['stroke', ['width', '0.1524'], ['type', 'solid']],
        ['fill', ['color', '0', '0', '0', '0']],
        ['uuid', sheet_uuid],
        ['property', 'Sheetname', module_name,
            ['at', f"{sheet_x:.3f}", f"{(sheet_y - 2.54):.3f}", "0"],
            ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'left', 'bottom']]
        ],
        ['property', 'Sheetfile', sheet_file_name,
            ['at', f"{sheet_x:.3f}", f"{(sheet_y + sheet_h + 2.54):.3f}", "0"],
            ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'left', 'top']]
        ]
    ]
    sheet_node.extend(sheet_pins_sexpr)
    # Register the sheet instance on the root sheet path (page 2), mirroring KiCad.
    sheet_node.append(['instances',
                       ['project', project_name,
                        ['path', f"/{root_uuid}", ['page', '2']]]])
    parent_children.append(sheet_node)

    # 9. Connect outside pins to sheet pins with local labels (name-based, not routed).
    # A local label at a pin's connection point connects it to every same-named label
    # on the sheet — no wires, so no routing congestion, T-junction bridges, or
    # dangling stubs. Placement uses the same engine as the sub-sheet: oriented away
    # from the symbol, L-stub fan for top/bottom pins, AABB-pushed clear of symbol
    # bodies (incl. pins/text), the sheet box, wires, and other labels, then a final
    # reconcile pass.
    parent_obstacles = []
    parent_symbol_boxes = {}    # ref -> body box (stub wires must not cross OTHER symbols)
    outside_centers = {}
    for ref, inst in outside_instances.items():
        defn = local_definitions.get(inst['lib_id'])
        lb = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, a_i, mx_i, my_i = get_symbol_instance_transform(inst['sexpr'])
        outside_centers[ref] = get_instance_aabb(lb, tx, ty, a_i, mx_i, my_i).center
        obox = _symbol_obstacle_box(inst['sexpr'], defn)
        parent_symbol_boxes[ref] = obox
        parent_obstacles.append(obox)
        parent_obstacles.extend(_property_box(inst['sexpr'], 'Reference', 'Value'))
    sheet_box = BoundingBox(sheet_x, sheet_y, sheet_x + sheet_w, sheet_y + sheet_h)
    sheet_center = (sheet_x + sheet_w / 2, sheet_y + sheet_h / 2)
    parent_obstacles.append(sheet_box.pad(_LABEL_GAP))
    for ch in parent_children:
        if isinstance(ch, list) and ch and ch[0] == 'wire':
            pts = next((q for q in ch[1:] if isinstance(q, list) and q[0] == 'pts'), None)
            if pts:
                xs = [(float(a[1]), float(a[2])) for a in pts[1:]
                      if isinstance(a, list) and len(a) > 2 and a[0] == 'xy']
                if len(xs) >= 2:
                    parent_obstacles.append(_wire_box(xs[0], xs[-1]))

    parent_label_recs = []
    parent_vfan = {}
    seen_labels = set()
    routed_wires_count = 0
    for root, coords_info in net_to_pin_coords.items():
        name = boundary_nets[root]['name']
        # sheet-pin label: its stub must not cross any outside symbol (the sheet is
        # its own owner, so the sheet box is not in cross).
        _place_label(parent_children, parent_label_recs, parent_obstacles, parent_vfan,
                     name, coords_info['parent_pin_x'], coords_info['parent_pin_y'],
                     sheet_center[0], sheet_center[1], 'label', None,
                     list(parent_symbol_boxes.values()))
        routed_wires_count += 1
        for p in boundary_nets[root]['outside_pins']:
            gk = grid_key(p['x'], p['y'])
            if (name, gk) in seen_labels:
                continue
            seen_labels.add((name, gk))
            cx_o, cy_o = outside_centers.get(p['ref'], (p['x'], p['y']))
            cross = [b for r, b in parent_symbol_boxes.items() if r != p['ref']]
            cross.append(sheet_box)
            _place_label(parent_children, parent_label_recs, parent_obstacles, parent_vfan,
                         name, p['x'], p['y'], cx_o, cy_o, 'label', None, cross)
            routed_wires_count += 1

    _reconcile_labels(parent_label_recs, parent_obstacles)

    # 10. Update sheet_instances in parent schematic
    # Look for existing sheet_instances or append to end
    sheet_instances_idx = -1
    for idx, child in enumerate(parent_children):
        if isinstance(child, list) and len(child) > 0 and child[0] == 'sheet_instances':
            sheet_instances_idx = idx
            break

    sub_path_expr = ['path', f"/{sheet_uuid}", ['page', '2']]

    if sheet_instances_idx != -1:
        parent_children[sheet_instances_idx].append(sub_path_expr)
    else:
        sheet_instances_expr = [
            'sheet_instances',
            ['path', '/', ['page', '1']],
            sub_path_expr
        ]
        parent_children.append(sheet_instances_expr)

    # Prune any dangling wire stubs left in the parent after cutting crossing nets
    parent_connector_gks = set()
    for ref, inst in outside_instances.items():
        for p in ref_to_pins.get(ref, []):
            parent_connector_gks.add(grid_key(p['x'], p['y']))
    for ch in parent_children:
        if not isinstance(ch, list) or not ch:
            continue
        if ch[0] in ('label', 'global_label', 'hierarchical_label', 'junction'):
            at = next((s for s in ch[1:] if isinstance(s, list) and s[0] == 'at'), None)
            if at:
                parent_connector_gks.add(grid_key(float(at[1]), float(at[2])))
        elif ch[0] == 'sheet':
            for s in ch[1:]:
                if isinstance(s, list) and s and s[0] == 'pin':
                    at = next((q for q in s[1:] if isinstance(q, list) and q[0] == 'at'), None)
                    if at:
                        parent_connector_gks.add(grid_key(float(at[1]), float(at[2])))
    prune_dangling_wires(parent_children, parent_connector_gks)
    parent_children = dedupe_wire_children(parent_children)

    # Save parent schematic back
    sch_sexpr = [sch_sexpr[0]] + parent_children
    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))

    return len(boundary_nets), routed_wires_count
