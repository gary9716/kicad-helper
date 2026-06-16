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


def create_module_from_components(schematic_path, table_path, components, module_name, sheet_file_name):
    project_dir = os.path.dirname(os.path.abspath(schematic_path))
    lib_map = load_sym_lib_table(table_path)

    # 1. Read parent schematic
    with open(schematic_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)

    if not sch_sexpr or sch_sexpr[0] != 'kicad_sch':
        raise ValueError(f"Invalid KiCad schematic file {schematic_path}")

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

    # Filter which elements to move
    moved_wires_nodes = [w[0] for w in wires if id(w[0]) in inside_wire_ids]
    moved_junctions_nodes = [j[0] for j in junctions if grid_key(j[1][0], j[1][1]) in inside_coords_all]
    moved_labels_nodes = [l[0] for l in labels if grid_key(l[2][0], l[2][1]) in inside_coords_all]
    moved_global_labels_nodes = [gl[0] for gl in global_labels if grid_key(gl[2][0], gl[2][1]) in inside_coords_all]

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
        sub_sch_children.append(inst_copy)

    # Move wires and shift them
    for w_node in moved_wires_nodes:
        w_copy = parse_sexpr(format_sexpr(w_node))
        shift_coordinates(w_copy, dx, dy)
        sub_sch_children.append(w_copy)

    # Move junctions and shift them
    for j_node in moved_junctions_nodes:
        j_copy = parse_sexpr(format_sexpr(j_node))
        shift_coordinates(j_copy, dx, dy)
        sub_sch_children.append(j_copy)

    # Move labels and shift them
    for l_node in moved_labels_nodes:
        l_copy = parse_sexpr(format_sexpr(l_node))
        shift_coordinates(l_copy, dx, dy)
        sub_sch_children.append(l_copy)

    # Copy global labels and shift them
    for gl_node in moved_global_labels_nodes:
        gl_copy = parse_sexpr(format_sexpr(gl_node))
        shift_coordinates(gl_copy, dx, dy)
        sub_sch_children.append(gl_copy)

    # Place Hierarchical Labels in sub-schematic
    # We place each label 5.08mm to the left or right of one of its inside pins
    net_to_pin_coords = {}
    for root, net_info in boundary_nets.items():
        # Find one inside pin
        p = net_info['inside_pins'][0]
        spx = p['x'] + dx
        spy = p['y'] + dy
        # Decide orientation based on average outside pin positions relative to cx
        outside_coords = [grid_to_coord(gk) for gk in net_info['coords'] if gk not in inside_coords_all]
        avg_out_x = sum(c[0] for c in outside_coords) / len(outside_coords) if outside_coords else cx
        
        if avg_out_x < cx:
            # Connects to left, put label 5.08mm to the left
            lx = spx - 5.08
            ly = spy
            angle = 180
            justify = 'right'
        else:
            # Connects to right, put label 5.08mm to the right
            lx = spx + 5.08
            ly = spy
            angle = 0
            justify = 'left'

        net_to_pin_coords[root] = {
            'inside_gx': p['x'],
            'inside_gy': p['y'],
            'side': 'left' if avg_out_x < cx else 'right'
        }

        # Add wire connection between pin and label
        w_expr = make_wire_sexpr(spx, spy, lx, ly)
        sub_sch_children.append(w_expr)

        # Add hierarchical label
        hl_expr = [
            'hierarchical_label', net_info['name'],
            ['shape', net_info['type']],
            ['at', f"{lx:.3f}", f"{ly:.3f}", str(angle)],
            ['effects', ['font', ['size', '1.27', '1.27']], ['justify', justify]],
            ['uuid', str(uuid.uuid4())]
        ]
        sub_sch_children.append(hl_expr)

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

    # Save child schematic
    with open(sub_sch_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sub_sch_children))

    # 8. Update Parent Schematic
    # Remove moved symbols, wires, junctions, and labels from parent
    moved_wire_ids = set(id(w) for w in moved_wires_nodes)
    moved_j_ids = set(id(j) for j in moved_junctions_nodes)
    moved_l_ids = set(id(l) for l in moved_labels_nodes)
    moved_inst_ids = set(id(inst['sexpr']) for inst in moved_instances.values())

    parent_children = []
    for child in sch_sexpr[1:]:
        if not isinstance(child, list) or len(child) == 0:
            parent_children.append(child)
            continue
        if child[0] == 'symbol' and id(child) in moved_inst_ids:
            continue
        if child[0] == 'wire' and (id(child) in moved_wire_ids or id(child) in crossing_wire_ids):
            continue
        if child[0] == 'junction' and id(child) in moved_j_ids:
            continue
        if child[0] == 'label' and id(child) in moved_l_ids:
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
    sheet_uuid = str(uuid.uuid4())

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
    parent_children.append(sheet_node)

    # 9. Route parent wires to connect outside elements to sheet pins
    # Bounding boxes for obstacles (excluding the sheet boundaries so routing can touch it)
    obstacles = []
    for ref, inst in outside_instances.items():
        defn = local_definitions.get(inst['lib_id'])
        local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst['sexpr'])
        global_bbox = get_instance_aabb(local_bbox, tx, ty, angle, mirror_x, mirror_y)
        obstacles.append(global_bbox)

    # Union-Find for remaining wires to find disjoint outside groups
    uf_outside = UnionFind()
    for child in parent_children:
        if isinstance(child, list) and child[0] == 'wire':
            pts_node = None
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'pts':
                    pts_node = sub
                    break
            if pts_node:
                x1_val, y1_val = None, None
                x2_val, y2_val = None, None
                for xy in pts_node[1:]:
                    if isinstance(xy, list) and len(xy) > 2 and xy[0] == 'xy':
                        if x1_val is None:
                            x1_val = float(xy[1])
                            y1_val = float(xy[2])
                        else:
                            x2_val = float(xy[1])
                            y2_val = float(xy[2])
                if x1_val is not None and x2_val is not None:
                    uf_outside.union(grid_key(x1_val, y1_val), grid_key(x2_val, y2_val))

    # Pin-grid blocklist: every outside pin + every sheet pin. Stops the router from
    # threading wires through pins or shorting adjacent nets (start/end auto-exempted).
    blocked_pin_grids = set()
    for ref in outside_instances:
        for p in ref_to_pins.get(ref, []):
            blocked_pin_grids.add(grid_key(p['x'], p['y']))
    for info in net_to_pin_coords.values():
        if 'parent_pin_x' in info:
            blocked_pin_grids.add(grid_key(info['parent_pin_x'], info['parent_pin_y']))

    routed_wires_count = 0
    for root, coords_info in net_to_pin_coords.items():
        pin_x = coords_info['parent_pin_x']
        pin_y = coords_info['parent_pin_y']

        # Outside coordinates on this net that we need to connect to the sheet pin
        outside_gks = [gk for gk in boundary_nets[root]['coords'] if gk not in inside_coords_all]
        if not outside_gks:
            continue

        # Group outside gks by their representative in uf_outside
        outside_groups = {}
        for gk in outside_gks:
            rep = uf_outside.find(gk)
            outside_groups.setdefault(rep, []).append(gk)

        # Connect each disjoint group to the sheet pin
        for group_gks in outside_groups.values():
            coords = [grid_to_coord(gk) for gk in group_gks]
            closest_coord = min(coords, key=lambda c: (c[0] - pin_x)**2 + (c[1] - pin_y)**2)

            # Try routing using A* pathfinding. Block other pins AND existing wires
            # (collinear) so re-routed nets don't overlap or short.
            end_dir = (1, 0) if net_to_pin_coords[root]['side'] == 'left' else (-1, 0)
            bwd = compute_blocked_wire_dirs(parent_children)
            bwd.pop(grid_key(*closest_coord), None)
            bwd.pop(grid_key(pin_x, pin_y), None)
            path = find_orthogonal_path(
                closest_coord, (pin_x, pin_y), obstacles,
                grid_size=1.27, required_end_dir=end_dir,
                blocked_pins=blocked_pin_grids, blocked_wires=bwd
            )
            if not path or len(path) <= 1:
                path = find_orthogonal_path(
                    closest_coord, (pin_x, pin_y), obstacles,
                    grid_size=1.27, required_end_dir=end_dir,
                    blocked_pins=blocked_pin_grids
                )
            if not path or len(path) <= 1:
                path = find_orthogonal_path(
                    closest_coord, (pin_x, pin_y), obstacles,
                    grid_size=1.27, required_end_dir=end_dir
                )

            if path and len(path) > 1:
                for i in range(len(path) - 1):
                    p1 = path[i]
                    p2 = path[i+1]
                    w_expr = make_wire_sexpr(p1[0], p1[1], p2[0], p2[1])
                    parent_children.append(w_expr)
                    routed_wires_count += 1
                    # Update uf_outside
                    uf_outside.union(grid_key(p1[0], p1[1]), grid_key(p2[0], p2[1]))
            else:
                # Fallback simple L-shape routing
                gx1_s = round(closest_coord[0] / 1.27) * 1.27
                gy1_s = round(closest_coord[1] / 1.27) * 1.27
                gx2_s = round(pin_x / 1.27) * 1.27
                gy2_s = round(pin_y / 1.27) * 1.27

                # Route horizontally first, then vertically
                if abs(gx1_s - gx2_s) > 0.01:
                    w1 = make_wire_sexpr(gx1_s, gy1_s, gx2_s, gy1_s)
                    parent_children.append(w1)
                    routed_wires_count += 1
                if abs(gy1_s - gy2_s) > 0.01:
                    w2 = make_wire_sexpr(gx2_s, gy1_s, gx2_s, gy2_s)
                    parent_children.append(w2)
                    routed_wires_count += 1
                uf_outside.union(grid_key(gx1_s, gy1_s), grid_key(gx2_s, gy2_s))

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
    parent_connector_gks = set(blocked_pin_grids)
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

    # Save parent schematic back
    sch_sexpr = [sch_sexpr[0]] + parent_children
    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))

    return len(boundary_nets), routed_wires_count
