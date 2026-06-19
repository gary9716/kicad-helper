import os
import math
import uuid
import heapq
from .parser import parse_sexpr, format_sexpr

class BoundingBox:
    def __init__(self, xmin=float('inf'), ymin=float('inf'), xmax=float('-inf'), ymax=float('-inf')):
        self.xmin = xmin
        self.ymin = ymin
        self.xmax = xmax
        self.ymax = ymax

    def update_point(self, x, y):
        self.xmin = min(self.xmin, x)
        self.ymin = min(self.ymin, y)
        self.xmax = max(self.xmax, x)
        self.ymax = max(self.ymax, y)

    def update_box(self, other):
        self.xmin = min(self.xmin, other.xmin)
        self.ymin = min(self.ymin, other.ymin)
        self.xmax = max(self.xmax, other.xmax)
        self.ymax = max(self.ymax, other.ymax)

    @property
    def width(self):
        return self.xmax - self.xmin if self.xmax >= self.xmin else 0

    @property
    def height(self):
        return self.ymax - self.ymin if self.ymax >= self.ymin else 0

    @property
    def center(self):
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def is_valid(self):
        return self.xmin <= self.xmax and self.ymin <= self.ymax

    def pad(self, margin):
        if not self.is_valid():
            return self
        return BoundingBox(self.xmin - margin, self.ymin - margin, self.xmax + margin, self.ymax + margin)

    def __repr__(self):
        return f"Box([{self.xmin:.2f}, {self.ymin:.2f}] -> [{self.xmax:.2f}, {self.ymax:.2f}], w={self.width:.2f}, h={self.height:.2f})"


def load_sym_lib_table(table_path):
    """
    Parses a KiCad sym-lib-table file and returns a mapping from library nicknames to absolute file paths.
    """
    lib_map = {}
    if not os.path.exists(table_path):
        return lib_map
    try:
        with open(table_path, 'r', encoding='utf-8') as f:
            content = f.read()
        table = parse_sexpr(content)
        if table and table[0] == 'sym_lib_table':
            for item in table[1:]:
                if isinstance(item, list) and item[0] == 'lib':
                    name = None
                    uri = None
                    for prop in item[1:]:
                        if isinstance(prop, list) and len(prop) > 1:
                            if prop[0] == 'name':
                                name = prop[1]
                            elif prop[0] == 'uri':
                                uri = prop[1]
                    if name and uri:
                        proj_dir = os.path.dirname(os.path.abspath(table_path))
                        # Expand KiCad project environment variable
                        uri = uri.replace('${KIPRJMOD}', proj_dir)
                        lib_map[name] = uri
    except Exception as e:
        print(f"Warning: failed to parse sym-lib-table at {table_path}: {e}")
    return lib_map


def find_symbol_definition(lib_name, sym_name, lib_map, project_dir):
    """
    Finds the S-expression list definition of a symbol by checking the local table and global paths.
    """
    import copy
    # 1. Search in local libraries
    lib_path = lib_map.get(lib_name)
    defn = None
    if lib_path:
        if not os.path.isabs(lib_path):
            lib_path = os.path.abspath(os.path.join(project_dir, lib_path))
        if os.path.exists(lib_path):
            defn = parse_symbol_from_file(lib_path, sym_name)
                
    # 2. Search in global KiCad installation path
    if not defn:
        global_path = f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/{lib_name}.kicad_sym"
        if os.path.exists(global_path):
            defn = parse_symbol_from_file(global_path, sym_name)
            
    if defn:
        extends_node = next((sub for sub in defn[1:] if isinstance(sub, list) and sub[0] == 'extends'), None)
        if extends_node:
            base_sym_name = extends_node[1]
            base_defn = find_symbol_definition(lib_name, base_sym_name, lib_map, project_dir)
            if base_defn:
                merged_defn = copy.deepcopy(defn)
                for base_sub in base_defn[1:]:
                    if isinstance(base_sub, list) and len(base_sub) > 0:
                        if base_sub[0] == 'symbol':
                            sub_copy = copy.deepcopy(base_sub)
                            # Rename "PARENT_0_1" / "PARENT_1_1" → "CHILD_0_1" / "CHILD_1_1"
                            # KiCad requires sub-symbol names to match the enclosing symbol name.
                            if len(sub_copy) > 1 and isinstance(sub_copy[1], str) and sub_copy[1].startswith(base_sym_name + '_'):
                                sub_copy[1] = sym_name + sub_copy[1][len(base_sym_name):]
                            merged_defn.append(sub_copy)
                        elif base_sub[0] not in ('property', 'extends', 'pin_names', 'pin_numbers'):
                            if not any(isinstance(x, list) and x[0] == base_sub[0] for x in merged_defn[1:]):
                                merged_defn.append(copy.deepcopy(base_sub))
                return merged_defn
    return defn


def parse_symbol_from_file(file_path, sym_name):
    """
    Finds and parses a specific symbol definition inside a .kicad_sym file.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        lib_sexpr = parse_sexpr(content)
        if lib_sexpr and lib_sexpr[0] == 'kicad_symbol_lib':
            for child in lib_sexpr[1:]:
                if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1 and child[1] == sym_name:
                    return child
    except Exception as e:
        print(f"Warning: failed to read symbol {sym_name} from {file_path}: {e}")
    return None


def get_symbol_local_bbox(symbol_def):
    """
    Computes the bounding box of a symbol at origin (0, 0) from its definition.
    """
    bbox = BoundingBox()
    
    def traverse(node):
        if not isinstance(node, list):
            return
            
        tag = node[0]
        if tag == 'symbol':
            for child in node[1:]:
                traverse(child)
        elif tag == 'rectangle':
            start_x, start_y = None, None
            end_x, end_y = None, None
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 2:
                    if p[0] == 'start':
                        start_x, start_y = float(p[1]), float(p[2])
                    elif p[0] == 'end':
                        end_x, end_y = float(p[1]), float(p[2])
            if start_x is not None and end_x is not None:
                bbox.update_point(start_x, start_y)
                bbox.update_point(end_x, end_y)
                
        elif tag == 'circle':
            center_x, center_y, radius = None, None, None
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 2 and p[0] == 'center':
                    center_x, center_y = float(p[1]), float(p[2])
                elif isinstance(p, list) and len(p) > 1 and p[0] == 'radius':
                    radius = float(p[1])
            if center_x is not None and radius is not None:
                bbox.update_point(center_x - radius, center_y - radius)
                bbox.update_point(center_x + radius, center_y + radius)
                
        elif tag == 'polyline':
            for p in node[1:]:
                if isinstance(p, list) and p[0] == 'pts':
                    for xy in p[1:]:
                        if isinstance(xy, list) and len(xy) > 2 and xy[0] == 'xy':
                            bbox.update_point(float(xy[1]), float(xy[2]))
                            
        elif tag == 'arc':
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 2 and p[0] in ('start', 'mid', 'end'):
                    bbox.update_point(float(p[1]), float(p[2]))
                     
    traverse(symbol_def)
    
    if not bbox.is_valid():
        # Fallback to a standard small box
        bbox = BoundingBox(-5.08, -5.08, 5.08, 5.08)
        
    return bbox


def get_symbol_instance_transform(symbol_inst):
    """
    Extracts translation, rotation, and mirror information from a symbol instance S-expression.
    """
    tx, ty, angle = 0.0, 0.0, 0.0
    mirror_x, mirror_y = False, False
    
    for child in symbol_inst[1:]:
        if isinstance(child, list) and len(child) > 0:
            if child[0] == 'at' and len(child) > 2:
                tx = float(child[1])
                ty = float(child[2])
                if len(child) > 3:
                    angle = float(child[3])
            elif child[0] == 'mirror' and len(child) > 1:
                if child[1] == 'x':
                    mirror_x = True
                elif child[1] == 'y':
                    mirror_y = True
                    
    return tx, ty, angle, mirror_x, mirror_y


def get_instance_aabb(local_bbox, tx, ty, angle, mirror_x=False, mirror_y=False):
    """
    Computes the global schematic Axis-Aligned Bounding Box (AABB) for a symbol instance.
    """
    corners = [
        (local_bbox.xmin, local_bbox.ymin),
        (local_bbox.xmax, local_bbox.ymin),
        (local_bbox.xmax, local_bbox.ymax),
        (local_bbox.xmin, local_bbox.ymax)
    ]
    
    global_bbox = BoundingBox()
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    
    for x, y in corners:
        if mirror_x:
            x = -x
        if mirror_y:
            y = -y
            
        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a
        
        global_bbox.update_point(rx + tx, ry + ty)
        
    return global_bbox


def find_orthogonal_path(start, end, obstacles, start_dir=None, grid_size=1.27, blocked_pins=None, blocked_wires=None, required_end_dir=None):
    # start: (x1, y1)
    # end: (x2, y2)
    # obstacles: list of BoundingBox objects
    
    # Grid coordinates as integers
    def to_grid(val):
        return int(round(val / grid_size))
    def to_val(coord):
        return coord * grid_size
        
    start_grid = (to_grid(start[0]), to_grid(start[1]))
    end_grid = (to_grid(end[0]), to_grid(end[1]))
    
    if start_grid == end_grid:
        return [start, end]
        
    # We define the search bounds dynamically
    all_x = [start_grid[0], end_grid[0]]
    all_y = [start_grid[1], end_grid[1]]
    for box in obstacles:
        all_x.append(to_grid(box.xmin))
        all_x.append(to_grid(box.xmax))
        all_y.append(to_grid(box.ymin))
        all_y.append(to_grid(box.ymax))
        
    min_x = min(all_x) - 20
    max_x = max(all_x) + 20
    min_y = min(all_y) - 20
    max_y = max(all_y) + 20
    
    # Helper to check if a grid point is inside any obstacle
    def is_blocked(gx, gy, dx=None, dy=None):
        # Exempt start and end points
        if (gx, gy) == start_grid or (gx, gy) == end_grid:
            return False
            
        if blocked_pins and (gx, gy) in blocked_pins:
            return True
            
        if blocked_wires and (gx, gy) in blocked_wires:
            if dx is not None and dy is not None:
                if (dx, dy) in blocked_wires[(gx, gy)]:
                    return True
            else:
                return True
            
        x = to_val(gx)
        y = to_val(gy)
        
        # We also want to leave a small gap around symbols
        # So we expand the symbol boxes by a margin of 1.27 mm
        margin = 1.27
        for box in obstacles:
            if (box.xmin - margin <= x <= box.xmax + margin and
                box.ymin - margin <= y <= box.ymax + margin):
                return True
        return False
        
    # A* algorithm
    # PQ element: (f_score, g_score, direction, current_node)
    # direction: (dx, dy)
    pq = []
    g_score = {}
    parent_map = {}
    
    # Push start node with allowed direction(s)
    if start_dir:
        h = abs(start_grid[0] - end_grid[0]) + abs(start_grid[1] - end_grid[1])
        heapq.heappush(pq, (h, 0.0, start_dir, start_grid))
        g_score[(start_grid, start_dir)] = 0.0
    else:
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            h = abs(start_grid[0] - end_grid[0]) + abs(start_grid[1] - end_grid[1])
            heapq.heappush(pq, (h, 0.0, (dx, dy), start_grid))
            g_score[(start_grid, (dx, dy))] = 0.0
        
    found_path = None
    
    while pq:
        f, g, dir_in, curr = heapq.heappop(pq)
        
        if curr == end_grid:
            if required_end_dir is None or dir_in == required_end_dir:
                found_path = (curr, dir_in)
                break
            continue
            
        state = (curr, dir_in)
        if g_score.get(state, float('inf')) < g:
            continue
        
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            # No moving backwards
            if (dx, dy) == (-dir_in[0], -dir_in[1]):
                continue
                
            nxt = (curr[0] + dx, curr[1] + dy)
            
            # Check bounds
            if not (min_x <= nxt[0] <= max_x and min_y <= nxt[1] <= max_y):
                continue
                
            # Check obstacles
            if is_blocked(nxt[0], nxt[1], dx, dy):
                continue
                
            # Cost: 1.0 for step + penalty for turn
            step_cost = 1.0
            if (dx, dy) != dir_in:
                step_cost += 15.0  # Heavy penalty for turns to keep wires straight!
                
            nxt_g = g + step_cost
            nxt_h = abs(nxt[0] - end_grid[0]) + abs(nxt[1] - end_grid[1])
            nxt_f = nxt_g + nxt_h
            
            nxt_state = (nxt, (dx, dy))
            if nxt_g < g_score.get(nxt_state, float('inf')):
                g_score[nxt_state] = nxt_g
                parent_map[nxt_state] = (curr, dir_in)
                heapq.heappush(pq, (nxt_f, nxt_g, (dx, dy), nxt))

    if found_path:
        path_pts = []
        curr, d = found_path
        while curr != start_grid:
            path_pts.append(curr)
            curr, d = parent_map[(curr, d)]
        path_pts.append(start_grid)
        path_pts.reverse()
        
        simplified = []
        if path_pts:
            simplified.append(path_pts[0])
            for i in range(1, len(path_pts) - 1):
                p_prev = path_pts[i-1]
                p_curr = path_pts[i]
                p_next = path_pts[i+1]
                
                dir1 = (p_curr[0] - p_prev[0], p_curr[1] - p_prev[1])
                dir2 = (p_next[0] - p_curr[0], p_next[1] - p_curr[1])
                
                if dir1 != dir2:
                    simplified.append(p_curr)
            simplified.append(path_pts[-1])
            
        return [(to_val(pt[0]), to_val(pt[1])) for pt in simplified]
    
    return None


def update_symbol_instance_position(symbol_inst, new_x, new_y):
    """
    Updates the position of a symbol instance in-place and translates its properties
    by the same displacement to keep them attached.
    """
    dx, dy = 0.0, 0.0
    found_at = False
    
    for child in symbol_inst[1:]:
        if isinstance(child, list) and len(child) > 0 and child[0] == 'at':
            old_x = float(child[1])
            old_y = float(child[2])
            dx = new_x - old_x
            dy = new_y - old_y
            child[1] = f"{new_x:.3f}"
            child[2] = f"{new_y:.3f}"
            found_at = True
            break
            
    if not found_at:
        return False
        
    # Translate properties
    for child in symbol_inst[1:]:
        if isinstance(child, list) and len(child) > 0 and child[0] == 'property':
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 0 and sub[0] == 'at':
                    px = float(sub[1])
                    py = float(sub[2])
                    sub[1] = f"{(px + dx):.3f}"
                    sub[2] = f"{(py + dy):.3f}"
                    
    return True


def resolve_overlaps(symbols, margin=2.54, max_iterations=150, tolerance=0.01):
    """
    Iteratively resolves overlaps between symbols using AABB collision resolution.
    symbols: list of dicts: {'uuid', 'local_bbox', 'tx', 'ty', 'angle', 'mirror_x', 'mirror_y', 'movable'}
    """
    grid = 1.27  # standard KiCad grid
    
    for iteration in range(max_iterations):
        # 1. Compute current padded global bounding boxes
        bboxes = {}
        for sym in symbols:
            padded_local = sym['local_bbox'].pad(margin)
            bboxes[sym['uuid']] = get_instance_aabb(
                padded_local, sym['tx'], sym['ty'], 
                sym['angle'], sym['mirror_x'], sym['mirror_y']
            )
            
        # 2. Accumulate displacements
        displacements = {sym['uuid']: [0.0, 0.0] for sym in symbols}
        overlap_counts = {sym['uuid']: 0 for sym in symbols}
        
        has_overlap = False
        
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                s1 = symbols[i]
                s2 = symbols[j]
                
                # If neither is movable, they cannot push each other
                if not s1['movable'] and not s2['movable']:
                    continue
                    
                b1 = bboxes[s1['uuid']]
                b2 = bboxes[s2['uuid']]
                
                # Check AABB intersection
                if (b1.xmin < b2.xmax and b1.xmax > b2.xmin and
                    b1.ymin < b2.ymax and b1.ymax > b2.ymin):
                    
                    has_overlap = True
                    
                    # Overlap depth
                    ox = min(b1.xmax, b2.xmax) - max(b1.xmin, b2.xmin)
                    oy = min(b1.ymax, b2.ymax) - max(b1.ymin, b2.ymin)
                    
                    # Resolve in direction of smaller overlap
                    dx, dy = 0.0, 0.0
                    if ox < oy:
                        c1_x = (b1.xmin + b1.xmax) / 2
                        c2_x = (b2.xmin + b2.xmax) / 2
                        dx = ox if c1_x <= c2_x else -ox
                    else:
                        c1_y = (b1.ymin + b1.ymax) / 2
                        c2_y = (b2.ymin + b2.ymax) / 2
                        dy = oy if c1_y <= c2_y else -oy
                        
                    # Apply forces
                    if s1['movable'] and s2['movable']:
                        displacements[s1['uuid']][0] -= dx / 2
                        displacements[s1['uuid']][1] -= dy / 2
                        displacements[s2['uuid']][0] += dx / 2
                        displacements[s2['uuid']][1] += dy / 2
                        overlap_counts[s1['uuid']] += 1
                        overlap_counts[s2['uuid']] += 1
                    elif s1['movable']:
                        displacements[s1['uuid']][0] -= dx
                        displacements[s1['uuid']][1] -= dy
                        overlap_counts[s1['uuid']] += 1
                    elif s2['movable']:
                        displacements[s2['uuid']][0] += dx
                        displacements[s2['uuid']][1] += dy
                        overlap_counts[s2['uuid']] += 1
                        
        if not has_overlap:
            break
            
        # 3. Apply displacements
        max_dist = 0.0
        for sym in symbols:
            if not sym['movable']:
                continue
            count = overlap_counts[sym['uuid']]
            if count > 0:
                # Average displacement to damp oscillations
                dx = displacements[sym['uuid']][0] / count
                dy = displacements[sym['uuid']][1] / count
                
                sym['tx'] += dx
                sym['ty'] += dy
                
                dist = math.sqrt(dx*dx + dy*dy)
                max_dist = max(max_dist, dist)
                
        if max_dist < tolerance:
            break
            
    # Snap to grid at the very end
    for sym in symbols:
        if sym['movable']:
            sym['tx'] = round(sym['tx'] / grid) * grid
            sym['ty'] = round(sym['ty'] / grid) * grid


def get_all_pins_from_symbol_def(symbol_def):
    """
    Traverses symbol_def and returns a list of dicts for all pins.
    """
    pins = []
    def traverse(node):
        if not isinstance(node, list):
            return
        tag = node[0]
        if tag == 'symbol':
            for child in node[1:]:
                traverse(child)
        elif tag == 'pin':
            pin_num = ""
            pin_name = ""
            x, y, orientation = 0.0, 0.0, 0.0
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 0:
                    if p[0] == 'number' and len(p) > 1:
                        pin_num = str(p[1])
                    elif p[0] == 'name' and len(p) > 1:
                        pin_name = str(p[1])
                    elif p[0] == 'at' and len(p) > 2:
                        x = float(p[1])
                        y = float(p[2])
                        if len(p) > 3:
                            orientation = float(p[3])
            pins.append({
                'name': pin_name,
                'number': pin_num,
                'x': x,
                'y': y,
                'orientation': orientation
            })
    traverse(symbol_def)
    return pins


def compute_pin_collision_boxes(pins, x, y, angle, pin_length=2.54):
    boxes = []
    for pin in pins:
        px = pin['x']
        py = pin['y']
        or_deg = pin['orientation']
        name_len = len(pin['name'])
        
        gpx, gpy = transform_pin_coordinate(px, py, x, y, angle)
        global_or = (or_deg + angle) % 360.0
        
        if abs(global_or - 0.0) < 1.0 or abs(global_or - 360.0) < 1.0:
            xmin = gpx
            xmax = gpx + pin_length + name_len * 1.0
            ymin = gpy - 1.27
            ymax = gpy + 1.27
        elif abs(global_or - 180.0) < 1.0:
            xmin = gpx - pin_length - name_len * 1.0
            xmax = gpx
            ymin = gpy - 1.27
            ymax = gpy + 1.27
        elif abs(global_or - 90.0) < 1.0:
            xmin = gpx - 1.27
            xmax = gpx + 1.27
            ymin = gpy
            ymax = gpy + pin_length + name_len * 1.0
        elif abs(global_or - 270.0) < 1.0:
            xmin = gpx - 1.27
            xmax = gpx + 1.27
            ymin = gpy - pin_length - name_len * 1.0
            ymax = gpy
        else:
            xmin = gpx - 2.54
            xmax = gpx + 2.54
            ymin = gpy - 2.54
            ymax = gpy + 2.54
            
        boxes.append((xmin, xmax, ymin, ymax))
    return boxes


def find_collision_free_position(x, y, angle, is_reference, local_bbox, pin_boxes, text_val):
    ymin, ymax = local_bbox.ymin, local_bbox.ymax
    local_dy = (ymin - 2.54) if is_reference else (ymax + 2.54)
    
    for perp_offset in [0.0, 2.54, 5.08]:
        if is_reference:
            curr_local_dy = local_dy - perp_offset
        else:
            curr_local_dy = local_dy + perp_offset
            
        base_x, base_y = transform_pin_coordinate(0, curr_local_dy, x, y, angle)
        candidate_shifts = [0.0, 2.54, -2.54, 5.08, -5.08, 7.62, -7.62, 10.16, -10.16, 12.70, -12.70, 15.24, -15.24]
        
        best_pos = (base_x, base_y)
        best_overlap_count = 9999
        
        for s in candidate_shifts:
            if abs(angle - 0.0) < 45.0 or abs(angle - 180.0) < 45.0 or abs(angle - 360.0) < 45.0:
                cx = base_x + s
                cy = base_y
                width = len(text_val) * 1.0
                height = 1.6
            else:
                cx = base_x
                cy = base_y + s
                width = 1.6
                height = len(text_val) * 1.0
                
            tx_min = cx - width / 2
            tx_max = cx + width / 2
            ty_min = cy - height / 2
            ty_max = cy + height / 2
            
            overlaps = 0
            for p_xmin, p_xmax, p_ymin, p_ymax in pin_boxes:
                if not (tx_max < p_xmin or tx_min > p_xmax or ty_max < p_ymin or ty_min > p_ymax):
                    overlaps += 1
                    
            if overlaps == 0:
                return cx, cy
                
            if overlaps < best_overlap_count:
                best_overlap_count = overlaps
                best_pos = (cx, cy)
                
    return best_pos


def adjust_symbol_properties_if_overlapping(symbol_inst, symbol_def):
    """
    Checks if Reference or Value properties of a symbol instance overlap with its pins.
    If so, shifts them to collision-free positions.
    """
    tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(symbol_inst)
    
    ref_node = None
    val_node = None
    ref_text = ""
    val_text = ""
    for child in symbol_inst[1:]:
        if isinstance(child, list) and len(child) > 0 and child[0] == 'property':
            if len(child) > 2:
                if child[1] == 'Reference':
                    ref_node = child
                    ref_text = child[2]
                elif child[1] == 'Value':
                    val_node = child
                    val_text = child[2]
                    
    if not ref_node or not val_node or not symbol_def:
        return
        
    local_bbox = get_symbol_local_bbox(symbol_def)
    pins = get_all_pins_from_symbol_def(symbol_def)
    if not pins:
        return
        
    pin_boxes = compute_pin_collision_boxes(pins, tx, ty, angle)
    
    def get_node_pos_and_box(node, text_val):
        for sub in node[1:]:
            if isinstance(sub, list) and len(sub) > 0 and sub[0] == 'at':
                cx = float(sub[1])
                cy = float(sub[2])
                if abs(angle - 0.0) < 45.0 or abs(angle - 180.0) < 45.0 or abs(angle - 360.0) < 45.0:
                    width = len(text_val) * 1.0
                    height = 1.6
                else:
                    width = 1.6
                    height = len(text_val) * 1.0
                return cx, cy, (cx - width/2, cx + width/2, cy - height/2, cy + height/2)
        return None, None, None
        
    ref_cx, ref_cy, ref_box = get_node_pos_and_box(ref_node, ref_text)
    val_cx, val_cy, val_box = get_node_pos_and_box(val_node, val_text)
    
    ref_overlaps = False
    if ref_box:
        for p_xmin, p_xmax, p_ymin, p_ymax in pin_boxes:
            if not (ref_box[1] < p_xmin or ref_box[0] > p_xmax or ref_box[3] < p_ymin or ref_box[2] > p_ymax):
                ref_overlaps = True
                break
                
    val_overlaps = False
    if val_box:
        for p_xmin, p_xmax, p_ymin, p_ymax in pin_boxes:
            if not (val_box[1] < p_xmin or val_box[0] > p_xmax or val_box[3] < p_ymin or val_box[2] > p_ymax):
                val_overlaps = True
                break
                
    if ref_overlaps:
        new_ref_x, new_ref_y = find_collision_free_position(tx, ty, angle, True, local_bbox, pin_boxes, ref_text)
        for sub in ref_node[1:]:
            if isinstance(sub, list) and len(sub) > 0 and sub[0] == 'at':
                sub[1] = f"{new_ref_x:.3f}"
                sub[2] = f"{new_ref_y:.3f}"
                
    if val_overlaps:
        new_val_x, new_val_y = find_collision_free_position(tx, ty, angle, False, local_bbox, pin_boxes, val_text)
        for sub in val_node[1:]:
            if isinstance(sub, list) and len(sub) > 0 and sub[0] == 'at':
                sub[1] = f"{new_val_x:.3f}"
                sub[2] = f"{new_val_y:.3f}"


def create_symbol_instance_sexpr(lib_id, reference, value, x, y, angle=0.0, properties_dict=None, local_bbox=None, symbol_def=None):
    """
    Creates an instance S-expression list for a symbol.
    """
    if local_bbox is None:
        ymin, ymax = -5.08, 5.08
        local_bbox = BoundingBox(ymin, -5.08, ymax, 5.08)
    else:
        ymin, ymax = local_bbox.ymin, local_bbox.ymax

    local_dy_ref = ymin - 2.54
    local_dy_val = ymax + 2.54

    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    ref_dx = -local_dy_ref * sin_a
    ref_dy = local_dy_ref * cos_a
    val_dx = -local_dy_val * sin_a
    val_dy = local_dy_val * cos_a

    ref_x = x + ref_dx
    ref_y = y + ref_dy
    val_x = x + val_dx
    val_y = y + val_dy

    if symbol_def is not None:
        pins = get_all_pins_from_symbol_def(symbol_def)
        if pins:
            pin_boxes = compute_pin_collision_boxes(pins, x, y, angle)
            ref_x, ref_y = find_collision_free_position(x, y, angle, True, local_bbox, pin_boxes, reference)
            val_x, val_y = find_collision_free_position(x, y, angle, False, local_bbox, pin_boxes, value)

    uid = str(uuid.uuid4())
    inst = [
        "symbol",
        ["lib_id", lib_id],
        ["at", f"{x:.3f}", f"{y:.3f}", f"{angle}"],
        ["unit", "1"],
        ["in_bom", "yes"],
        ["on_board", "yes"],
        ["uuid", uid],
        ["property", "Reference", reference,
            ["at", f"{ref_x:.3f}", f"{ref_y:.3f}", f"{angle}"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["property", "Value", value,
            ["at", f"{val_x:.3f}", f"{val_y:.3f}", f"{angle}"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ]
    ]
    
    properties_dict = properties_dict or {}
    footprint = properties_dict.get("Footprint", "")
    datasheet = properties_dict.get("Datasheet", "")
    
    inst.append(
        ["property", "Footprint", footprint,
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["hide", "yes"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ]
    )
    inst.append(
        ["property", "Datasheet", datasheet,
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["hide", "yes"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ]
    )
    
    # Custom attributes
    for k, v in properties_dict.items():
        if k in ("Reference", "Value", "Footprint", "Datasheet"):
            continue
        inst.append(
            ["property", k, v,
                ["at", f"{x:.3f}", f"{y:.3f}", "0"],
                ["hide", "yes"],
                ["effects", ["font", ["size", "1.27", "1.27"]]]
            ]
        )
        
    return inst


def get_or_create_lib_symbols(sch_sexpr):
    """
    Finds the lib_symbols block in schematic or creates one if it doesn't exist.
    """
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and len(child) > 0 and child[0] == 'lib_symbols':
            return child
    lib_syms = ['lib_symbols']
    sch_sexpr.insert(5, lib_syms)
    return lib_syms


def add_symbol_def_to_schematic(sch_sexpr, symbol_def):
    """
    Appends a symbol definition to the schematic's lib_symbols section.
    Replaces existing one with same name if already present.
    """
    lib_syms = get_or_create_lib_symbols(sch_sexpr)
    symbol_name = symbol_def[1]
    
    # Check if definition already exists
    for idx, child in enumerate(lib_syms[1:], 1):
        if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1 and child[1] == symbol_name:
            lib_syms[idx] = symbol_def
            return
            
    lib_syms.append(symbol_def)


def _add_instance_metadata(inst, defn, root_uuid, project_name, reference):
    """Append the per-pin uuid entries and the `instances` block KiCad needs to
    register a symbol on the root sheet's instance path.

    Without this block the symbol's pins are not part of the sheet connection
    graph, so a plain wire to a pin is reported `wire_dangling` by ERC even though
    the pin is geometrically on the wire. (A net label sidesteps it by carrying
    its own net name — which is why label-routing worked around the missing block.)
    """
    if defn:
        seen = set()
        for p in get_all_pins_from_symbol_def(defn):
            num = p.get('number')
            if num and num not in seen:
                seen.add(num)
                inst.append(['pin', num, ['uuid', str(uuid.uuid4())]])
    inst.append([
        'instances',
        ['project', project_name,
            ['path', f'/{root_uuid}',
                ['reference', reference],
                ['unit', '1'],
            ],
        ],
    ])


def place_symbols_and_resolve(schematic_path, table_path, new_placements, margin=2.54, resolve=True,
                              bbox_overrides=None):
    """
    Places new symbols in the schematic, parses all symbols' bounds, resolves overlaps, and writes back.

    bbox_overrides: optional {reference: BoundingBox} of local bounding boxes to use
    for overlap resolution instead of the symbol's own body box. Callers pass boxes
    padded for net-label text so symbols are spread far enough apart that labels do
    not collide. Symbol positions still snap to grid.

    new_placements: list of dicts:
    [
        {
            "lib_id": "LibraryNickName:SymbolName",
            "reference": "U1",
            "value": "MyChip",
            "x": 120.0,
            "y": 80.0,
            "angle": 0.0,
            "properties": {"Footprint": "Package:DIP-8"}
        }
    ]
    """
    project_dir = os.path.dirname(os.path.abspath(schematic_path))
    lib_map = load_sym_lib_table(table_path)
    
    # Read and parse schematic
    with open(schematic_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)
    
    if not sch_sexpr or sch_sexpr[0] != 'kicad_sch':
        raise ValueError(f"Invalid KiCad schematic file {schematic_path}")

    # Root sheet uuid + project name drive each symbol's instances/path block.
    root_uuid = next((c[1] for c in sch_sexpr[1:]
                      if isinstance(c, list) and c and c[0] == 'uuid'), None)
    if root_uuid is None:
        root_uuid = str(uuid.uuid4())
        sch_sexpr.insert(1, ['uuid', root_uuid])
    project_name = os.path.splitext(os.path.basename(schematic_path))[0]

    # Remove existing instances of the symbols we are placing to avoid duplicates
    new_refs = {p['reference'] for p in new_placements}
    filtered_children = []
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            is_instance = False
            ref_val = None
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == 'lib_id':
                        is_instance = True
                    elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                        ref_val = sub[2]
            if is_instance and ref_val in new_refs:
                continue
        filtered_children.append(child)
    sch_sexpr = [sch_sexpr[0]] + filtered_children
        
    lib_symbols = get_or_create_lib_symbols(sch_sexpr)
    
    # 1. Parse existing symbol library definitions from schematic
    # Map from lib_id (e.g. Device:R) to its S-expression definition
    local_definitions = {}
    for child in lib_symbols[1:]:
        if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1:
            local_definitions[child[1]] = child
            
    # 2. Add new symbol definitions if needed
    for placement in new_placements:
        lib_id = placement['lib_id']
        if ':' not in lib_id:
            raise ValueError(f"lib_id must be in 'Library:Symbol' format, got '{lib_id}'")
            
        lib_name, sym_name = lib_id.split(':', 1)
        
        # Always reload definition from external library to get latest size/pins
        defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        if defn:
            defn[1] = lib_id  # Prefix definition name with the library nickname
            add_symbol_def_to_schematic(sch_sexpr, defn)
            local_definitions[lib_id] = defn
        elif lib_id not in local_definitions:
            raise ValueError(f"Could not find symbol definition for {lib_id} in local table or global paths.")
            
    # 3. Create the new symbol instances in the schematic AST
    new_instances = []
    for placement in new_placements:
        lib_id = placement['lib_id']
        defn = local_definitions.get(lib_id)
        local_bbox = get_symbol_local_bbox(defn) if defn else None
        
        raw_x = placement.get('x', 100.0)
        raw_y = placement.get('y', 100.0)
        x = round(raw_x / 1.27) * 1.27
        y = round(raw_y / 1.27) * 1.27
        if abs(x - raw_x) > 1e-4 or abs(y - raw_y) > 1e-4:
            print(f"Note: Snapping symbol {placement['reference']} placement from ({raw_x}, {raw_y}) to grid ({x:.3f}, {y:.3f})")

        inst = create_symbol_instance_sexpr(
            lib_id=lib_id,
            reference=placement['reference'],
            value=placement['value'],
            x=x,
            y=y,
            angle=placement.get('angle', 0.0),
            properties_dict=placement.get('properties'),
            local_bbox=local_bbox,
            symbol_def=defn
        )
        _add_instance_metadata(inst, defn, root_uuid, project_name, placement['reference'])
        sch_sexpr.append(inst)
        new_instances.append(inst)
        
    # 4. Gather ALL symbols in schematic (existing + new) for overlap resolution
    symbols_for_overlap = []
    
    # Parse existing ones from schematic AST
    # These are elements at root level starting with 'symbol' and having a 'lib_id' child
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            # Check if it is an instance (contains lib_id) or definition (lib_symbols section)
            # Since lib_symbols section contains nested symbols, they are not at root level.
            # So all root-level 'symbol' nodes are instances!
            lib_id_node = None
            uuid_node = None
            ref_val = None
            
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == 'lib_id':
                        lib_id_node = sub[1]
                    elif sub[0] == 'uuid':
                        uuid_node = sub[1]
                    elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                        ref_val = sub[2]
                        
            if lib_id_node and uuid_node:
                # Find its definition to compute local bounding box
                defn = local_definitions.get(lib_id_node)
                if not defn:
                    # Look up if possible, otherwise fallback
                    if ':' in lib_id_node:
                        lib_name, sym_name = lib_id_node.split(':', 1)
                        defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
                        if defn:
                            add_symbol_def_to_schematic(sch_sexpr, defn)
                            local_definitions[lib_id_node] = defn
                            
                local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
                if bbox_overrides and ref_val in bbox_overrides:
                    local_bbox = bbox_overrides[ref_val]
                tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(child)

                # Check if this instance is one of the newly added ones
                is_new = child in new_instances
                
                symbols_for_overlap.append({
                    'uuid': uuid_node,
                    'ref': ref_val or "Unknown",
                    'lib_id': lib_id_node,
                    'sexpr': child,
                    'local_bbox': local_bbox,
                    'tx': tx,
                    'ty': ty,
                    'angle': angle,
                    'mirror_x': mirror_x,
                    'mirror_y': mirror_y,
                    'movable': is_new  # ONLY new symbols can move! Existing layout is preserved.
                })
                
    # 5. Resolve overlaps if requested
    if resolve and len(symbols_for_overlap) > 1:
        resolve_overlaps(symbols_for_overlap, margin=margin)
        
        # Write updated positions back to AST
        for sym in symbols_for_overlap:
            if sym['movable']:
                update_symbol_instance_position(sym['sexpr'], sym['tx'], sym['ty'])
            defn = local_definitions.get(sym.get('lib_id'))
            if defn:
                adjust_symbol_properties_if_overlapping(sym['sexpr'], defn)
                
    # 6. Write back to file
    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))
        
    return symbols_for_overlap


def find_pin_local_data(symbol_def, pin_ref):
    """
    Finds a pin in a symbol definition by pin number or pin name.
    Returns (px, py, orientation) or None.
    """
    matching_pin = None
    
    def traverse(node):
        nonlocal matching_pin
        if not isinstance(node, list):
            return
            
        tag = node[0]
        if tag == 'symbol':
            for child in node[1:]:
                traverse(child)
        elif tag == 'pin':
            pin_num = None
            pin_name = None
            x, y, orientation = 0.0, 0.0, 0.0
            
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 0:
                    if p[0] == 'number' and len(p) > 1:
                        pin_num = p[1]
                    elif p[0] == 'name' and len(p) > 1:
                        pin_name = p[1]
                    elif p[0] == 'at' and len(p) > 2:
                        x = float(p[1])
                        y = float(p[2])
                        if len(p) > 3:
                            orientation = float(p[3])
                            
            if pin_num == pin_ref or pin_name == pin_ref:
                matching_pin = (x, y, orientation)
                
    traverse(symbol_def)
    return matching_pin


def transform_pin_coordinate(px, py, tx, ty, angle, mirror_x=False, mirror_y=False):
    """
    Transforms local pin coordinates to global schematic coordinates.

    Symbol-library pin coordinates use a Y-up convention; the schematic canvas is
    Y-down. KiCad applies this inherent vertical flip when instantiating a symbol,
    so we negate py before mirror/rotation. Without it, every pin lands mirrored
    about the symbol's origin and KiCad ERC reports the pins/labels unconnected.
    """
    py = -py
    if mirror_x:
        px = -px
    if mirror_y:
        py = -py
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    rx = px * cos_a - py * sin_a
    ry = px * sin_a + py * cos_a
    return rx + tx, ry + ty


def make_wire_sexpr(x1, y1, x2, y2):
    """
    Generates a wire S-expression list.
    """
    uid = str(uuid.uuid4())
    return [
        "wire",
        ["pts", ["xy", f"{x1:.3f}", f"{y1:.3f}"], ["xy", f"{x2:.3f}", f"{y2:.3f}"]],
        ["stroke", ["width", "0"], ["type", "default"]],
        ["uuid", uid]
    ]


def _wire_grid_endpoints(wire_node, grid=1.27):
    """Return a frozenset of the wire's two grid-snapped endpoints, or None."""
    pts = next((s for s in wire_node[1:] if isinstance(s, list) and s[0] == 'pts'), None)
    if not pts:
        return None
    cs = [(float(a[1]), float(a[2])) for a in pts[1:]
          if isinstance(a, list) and len(a) > 2 and a[0] == 'xy']
    if len(cs) < 2:
        return None
    a = (int(round(cs[0][0] / grid)), int(round(cs[0][1] / grid)))
    b = (int(round(cs[-1][0] / grid)), int(round(cs[-1][1] / grid)))
    return frozenset((a, b))


def dedupe_wire_children(children):
    """Drop wire nodes whose (unordered, grid-snapped) endpoints duplicate an earlier wire.
    Identical overlapping segments are always redundant; keep the first, drop the rest.
    Operates on a list of child nodes (no root tag)."""
    seen = set()
    out = []
    for ch in children:
        if isinstance(ch, list) and ch and ch[0] == 'wire':
            ep = _wire_grid_endpoints(ch)
            if ep is not None:
                if ep in seen:
                    continue
                seen.add(ep)
        out.append(ch)
    return out


def get_wire_grid_points(x1, y1, x2, y2, grid_size=1.27):
    pts = []
    gx1 = int(round(x1 / grid_size))
    gy1 = int(round(y1 / grid_size))
    gx2 = int(round(x2 / grid_size))
    gy2 = int(round(y2 / grid_size))
    
    if gx1 == gx2:
        for gy in range(min(gy1, gy2), max(gy1, gy2) + 1):
            pts.append((gx1, gy))
    elif gy1 == gy2:
        for gx in range(min(gx1, gx2), max(gx1, gx2) + 1):
            pts.append((gx, gy1))
    else:
        pts.append((gx1, gy1))
        pts.append((gx2, gy2))
    return pts


def connect_symbols_in_schematic(schematic_path, table_path, connections, orthogonal=True):
    """
    Connections is a list of dicts: [{"from": "U101:1", "to": "U102:2"}]
    """
    project_dir = os.path.dirname(os.path.abspath(schematic_path))
    lib_map = load_sym_lib_table(table_path)
    
    with open(schematic_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)
    
    if not sch_sexpr or sch_sexpr[0] != 'kicad_sch':
        raise ValueError(f"Invalid KiCad schematic file {schematic_path}")
        
    lib_symbols = get_or_create_lib_symbols(sch_sexpr)
    
    # 1. Parse existing symbol library definitions from schematic
    local_definitions = {}
    for child in lib_symbols[1:]:
        if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1:
            local_definitions[child[1]] = child
            
    # 2. Parse all symbol instances currently in the schematic
    instances_by_ref = {}
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
                instances_by_ref[ref_val] = {
                    'sexpr': child,
                    'lib_id': lib_id_val
                }
                
    # Build list of symbol bounding boxes as obstacles, and collect all pin coordinates
    symbol_boxes = []
    all_pin_coords = []
    
    for ref_val, inst in instances_by_ref.items():
        defn = local_definitions.get(inst['lib_id'])
        if not defn and inst['lib_id'] and ':' in inst['lib_id']:
            lib_name, sym_name = inst['lib_id'].split(':', 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            
        local_bbox = get_symbol_local_bbox(defn) if defn else BoundingBox(-5.08, -5.08, 5.08, 5.08)
        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst['sexpr'])
        global_bbox = get_instance_aabb(local_bbox, tx, ty, angle, mirror_x, mirror_y)
        symbol_boxes.append(global_bbox)
        
        # Collect pin coordinates for obstacle blocking
        if defn:
            pins_in_defn = []
            def traverse_pins(node):
                if not isinstance(node, list):
                    return
                if node[0] == 'pin':
                    px, py = None, None
                    for p in node[1:]:
                        if isinstance(p, list) and p[0] == 'at' and len(p) > 2:
                            px = float(p[1])
                            py = float(p[2])
                            break
                    if px is not None:
                        pins_in_defn.append((px, py))
                else:
                    for child in node[1:]:
                        traverse_pins(child)
            traverse_pins(defn)
            
            for px, py in pins_in_defn:
                gx, gy = transform_pin_coordinate(px, py, tx, ty, angle, mirror_x, mirror_y)
                all_pin_coords.append((ref_val, gx, gy))
                
    # 3. For each connection, find pin global coordinates and add wires
    new_wires = []
    for conn in connections:
        from_str = conn['from']
        to_str = conn['to']
        
        if ':' not in from_str or ':' not in to_str:
            print(f"Warning: Invalid connection format '{from_str}' or '{to_str}'. Must be 'Ref:Pin'. Skipping.")
            continue
            
        from_ref, from_pin = from_str.split(':', 1)
        to_ref, to_pin = to_str.split(':', 1)
        
        if from_ref not in instances_by_ref:
            print(f"Warning: Symbol instance '{from_ref}' not found in schematic. Skipping connection.")
            continue
        if to_ref not in instances_by_ref:
            print(f"Warning: Symbol instance '{to_ref}' not found in schematic. Skipping connection.")
            continue
            
        inst1 = instances_by_ref[from_ref]
        inst2 = instances_by_ref[to_ref]
        
        # Look up definitions
        defn1 = local_definitions.get(inst1['lib_id'])
        if not defn1 and inst1['lib_id'] and ':' in inst1['lib_id']:
            lib_name, sym_name = inst1['lib_id'].split(':', 1)
            defn1 = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            if defn1:
                defn1[1] = inst1['lib_id']  # Prefix definition name with the library nickname
                add_symbol_def_to_schematic(sch_sexpr, defn1)
                local_definitions[inst1['lib_id']] = defn1
                
        defn2 = local_definitions.get(inst2['lib_id'])
        if not defn2 and inst2['lib_id'] and ':' in inst2['lib_id']:
            lib_name, sym_name = inst2['lib_id'].split(':', 1)
            defn2 = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            if defn2:
                defn2[1] = inst2['lib_id']  # Prefix definition name with the library nickname
                add_symbol_def_to_schematic(sch_sexpr, defn2)
                local_definitions[inst2['lib_id']] = defn2
                
        if not defn1 or not defn2:
            print(f"Warning: Could not find definitions for '{from_ref}' or '{to_ref}'. Skipping.")
            continue
            
        pin1_data = find_pin_local_data(defn1, from_pin)
        pin2_data = find_pin_local_data(defn2, to_pin)
        
        if not pin1_data:
            print(f"Warning: Pin '{from_pin}' not found in symbol definition for '{from_ref}'. Skipping.")
            continue
        if not pin2_data:
            print(f"Warning: Pin '{to_pin}' not found in symbol definition for '{to_ref}'. Skipping.")
            continue
            
        # Transform pin coordinates to global schematic coordinates
        tx1, ty1, angle1, mirror_x1, mirror_y1 = get_symbol_instance_transform(inst1['sexpr'])
        tx2, ty2, angle2, mirror_x2, mirror_y2 = get_symbol_instance_transform(inst2['sexpr'])
        
        gx1, gy1 = transform_pin_coordinate(pin1_data[0], pin1_data[1], tx1, ty1, angle1, mirror_x1, mirror_y1)
        gx2, gy2 = transform_pin_coordinate(pin2_data[0], pin2_data[1], tx2, ty2, angle2, mirror_x2, mirror_y2)
        
        # Do not delete existing wires to avoid destroying multi-point nets (VCC, GND, reset pull-ups, etc.)
        to_delete = set()
        
        # Generate wire S-expression(s)
        orientation1 = (pin1_data[2] + angle1) % 360  # Global orientation of source pin
        
        # Collect and snap blocked pin coordinates (excluding current start/end pins)
        blocked_pin_grids = set()
        for ref_v, px_g, py_g in all_pin_coords:
            gpx = int(round(px_g / 1.27))
            gpy = int(round(py_g / 1.27))
            
            # Skip current start and end pins
            if ref_v == from_ref and abs(px_g - gx1) < 0.05 and abs(py_g - gy1) < 0.05:
                continue
            if ref_v == to_ref and abs(px_g - gx2) < 0.05 and abs(py_g - gy2) < 0.05:
                continue
                
            blocked_pin_grids.add((gpx, gpy))

        # Collect existing wire segments (grid coords) and build a net model so we can
        # tell which wires already belong to the net being connected.
        existing_segs = []
        for child in sch_sexpr[1:]:
            if isinstance(child, list) and child[0] == 'wire':
                pts_node = next((sub for sub in child[1:] if isinstance(sub, list) and sub[0] == 'pts'), None)
                if not pts_node:
                    continue
                cs = [(float(a[1]), float(a[2])) for a in pts_node[1:]
                      if isinstance(a, list) and len(a) > 2 and a[0] == 'xy']
                if len(cs) >= 2:
                    existing_segs.append(((int(round(cs[0][0] / 1.27)), int(round(cs[0][1] / 1.27))),
                                          (int(round(cs[-1][0] / 1.27)), int(round(cs[-1][1] / 1.27)))))

        _net = {}
        def _net_find(n):
            _net.setdefault(n, n)
            while _net[n] != n:
                _net[n] = _net[_net[n]]
                n = _net[n]
            return n
        for g1, g2 in existing_segs:
            _net[_net_find(g1)] = _net_find(g2)
        start_g = (int(round(gx1 / 1.27)), int(round(gy1 / 1.27)))
        end_g = (int(round(gx2 / 1.27)), int(round(gy2 / 1.27)))
        current_net_roots = {_net_find(start_g), _net_find(end_g)}

        # Build blocked_wire_directions to forbid collinear overlap — but ONLY for wires
        # NOT on the net we're connecting. Joining your own net is legal; overlapping a
        # different net is an electrical short. Net-aware blocking lets the full A* succeed
        # for multi-pin nets instead of falling through to overlap-blind fallbacks.
        blocked_wire_directions = {}
        for (wg1, wg2) in existing_segs:
            if _net_find(wg1) in current_net_roots or _net_find(wg2) in current_net_roots:
                continue
            wgx1, wgy1 = wg1
            wgx2, wgy2 = wg2
            if wgx1 == wgx2:
                # Vertical wire
                dirs = {(0, 1), (0, -1)}
                for gy in range(min(wgy1, wgy2), max(wgy1, wgy2) + 1):
                    blocked_wire_directions.setdefault((wgx1, gy), set()).update(dirs)
            elif wgy1 == wgy2:
                # Horizontal wire
                dirs = {(1, 0), (-1, 0)}
                for gx in range(min(wgx1, wgx2), max(wgx1, wgx2) + 1):
                    blocked_wire_directions.setdefault((gx, wgy1), set()).update(dirs)
            else:
                # Diagonal/other wire: block all directions
                dirs = {(1, 0), (-1, 0), (0, 1), (0, -1)}
                blocked_wire_directions.setdefault((wgx1, wgy1), set()).update(dirs)
                blocked_wire_directions.setdefault((wgx2, wgy2), set()).update(dirs)

        # Ensure start and end pins of current connection are not blocked
        start_grid = (int(round(gx1 / 1.27)), int(round(gy1 / 1.27)))
        end_grid = (int(round(gx2 / 1.27)), int(round(gy2 / 1.27)))
        blocked_wire_directions.pop(start_grid, None)
        blocked_wire_directions.pop(end_grid, None)

        if orthogonal:
            # Map global orientation of source pin to start direction away from symbol body
            start_dir = None
            if orientation1 == 0:
                start_dir = (-1, 0)
            elif orientation1 == 180:
                start_dir = (1, 0)
            elif orientation1 == 90:
                start_dir = (0, -1)
            elif orientation1 == 270:
                start_dir = (0, 1)
                
            # Try to route using A* pathfinding with both pin and wire direction blocking
            path = find_orthogonal_path(
                (gx1, gy1), (gx2, gy2), symbol_boxes, 
                start_dir=start_dir, grid_size=1.27, 
                blocked_pins=blocked_pin_grids,
                blocked_wires=blocked_wire_directions
            )
            
            if not path or len(path) <= 1:
                # Fallback: relax only the forced start direction. KEEP pin blocking AND
                # net-aware wire blocking — routing through another pin or over a different
                # net are both electrical shorts, so those constraints must never be dropped.
                path = find_orthogonal_path(
                    (gx1, gy1), (gx2, gy2), symbol_boxes,
                    grid_size=1.27,
                    blocked_pins=blocked_pin_grids,
                    blocked_wires=blocked_wire_directions
                )

            if path and len(path) > 1:
                for i in range(len(path) - 1):
                    p1 = path[i]
                    p2 = path[i+1]
                    wire_expr = make_wire_sexpr(p1[0], p1[1], p2[0], p2[1])
                    new_wires.append(wire_expr)
                    sch_sexpr.append(wire_expr)
            else:
                # Fallback 2: simple L-shape routing
                is_horizontal_pin = (orientation1 in (0.0, 180.0, 360.0))
                grid = 1.27
                gx1_s, gy1_s = round(gx1 / grid) * grid, round(gy1 / grid) * grid
                gx2_s, gy2_s = round(gx2 / grid) * grid, round(gy2 / grid) * grid
                
                if is_horizontal_pin:
                    if abs(gx1_s - gx2_s) > 0.01:
                        w = make_wire_sexpr(gx1_s, gy1_s, gx2_s, gy1_s)
                        new_wires.append(w)
                        sch_sexpr.append(w)
                    if abs(gy1_s - gy2_s) > 0.01:
                        w = make_wire_sexpr(gx2_s, gy1_s, gx2_s, gy2_s)
                        new_wires.append(w)
                        sch_sexpr.append(w)
                else:
                    if abs(gy1_s - gy2_s) > 0.01:
                        w = make_wire_sexpr(gx1_s, gy1_s, gx1_s, gy2_s)
                        new_wires.append(w)
                        sch_sexpr.append(w)
                    if abs(gx1_s - gx2_s) > 0.01:
                        w = make_wire_sexpr(gx1_s, gy2_s, gx2_s, gy2_s)
                        new_wires.append(w)
                        sch_sexpr.append(w)
        else:
            # Straight diagonal line
            w = make_wire_sexpr(gx1, gy1, gx2, gy2)
            new_wires.append(w)
            sch_sexpr.append(w)
            
    # Drop any exact-duplicate wire segments produced while routing multi-pin nets.
    sch_sexpr = [sch_sexpr[0]] + dedupe_wire_children(sch_sexpr[1:])

    # Save schematic
    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))

    return len(new_wires)


# ── Annotation helpers ────────────────────────────────────────────────────────

def make_global_label_sexpr(name, shape, x, y, angle):
    """Create a global_label S-expression list."""
    justify = "right" if int(angle) == 180 else "left"
    uid = str(uuid.uuid4())
    return [
        "global_label", name,
        ["shape", shape],
        ["at", f"{x:.3f}", f"{y:.3f}", str(int(angle))],
        ["fields_autoplaced", "yes"],
        ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", justify]],
        ["uuid", uid],
        ["property", "Intersheetrefs", "",
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["hide", "yes"],
            ["show_name", "no"],
            ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", justify]]
        ]
    ]


def make_junction_sexpr(x, y):
    """Create a junction S-expression list."""
    uid = str(uuid.uuid4())
    return ["junction",
        ["at", f"{x:.3f}", f"{y:.3f}"],
        ["diameter", "0"],
        ["color", "0", "0", "0", "0"],
        ["uuid", uid]
    ]


def make_no_connect_sexpr(x, y):
    """Create a no_connect S-expression list."""
    uid = str(uuid.uuid4())
    return ["no_connect", ["at", f"{x:.3f}", f"{y:.3f}"], ["uuid", uid]]


def _get_sch_uuid(sch_sexpr):
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'uuid' and len(child) > 1:
            return child[1]
    return str(uuid.uuid4())


def _next_pwr_ref(sch_sexpr):
    """Return next unused #PWRNN reference designator."""
    existing = set()
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                    ref = sub[2]
                    if ref.startswith('#PWR'):
                        try:
                            existing.add(int(ref[4:]))
                        except ValueError:
                            pass
    n = 1
    while n in existing:
        n += 1
    return f"#PWR{n:02d}"


def _make_power_instance(name, x, y, pwr_ref, sch_uuid, project_name):
    """Create a power symbol instance S-expression list."""
    lib_id = f"power:{name}"
    uid = str(uuid.uuid4())
    uid_pin = str(uuid.uuid4())
    return [
        "symbol",
        ["lib_id", lib_id],
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        ["unit", "1"],
        ["body_style", "1"],
        ["exclude_from_sim", "no"],
        ["in_bom", "yes"],
        ["on_board", "yes"],
        ["in_pos_files", "yes"],
        ["dnp", "no"],
        ["uuid", uid],
        ["property", "Reference", pwr_ref,
            ["at", f"{x:.3f}", f"{y+1.27:.3f}", "0"],
            ["hide", "yes"], ["show_name", "no"], ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["property", "Value", name,
            ["at", f"{x:.3f}", f"{y+2.54:.3f}", "0"],
            ["show_name", "no"], ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["property", "Footprint", "",
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["hide", "yes"], ["show_name", "no"], ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["property", "Datasheet", "",
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["hide", "yes"], ["show_name", "no"], ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["property", "Description", "",
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["show_name", "no"], ["do_not_autoplace", "no"],
            ["effects", ["font", ["size", "1.27", "1.27"]]]
        ],
        ["pin", "1", ["uuid", uid_pin]],
        ["instances",
            ["project", project_name,
                ["path", f"/{sch_uuid}",
                    ["reference", pwr_ref],
                    ["unit", "1"]
                ]
            ]
        ]
    ]


def _side_to_angle(side):
    return {"left": 180, "right": 0, "up": 90, "down": 270}.get(side.lower(), 0)


def _resolve_pin_position(pin_str, instances_by_ref, local_definitions, lib_map, project_dir):
    """Resolve 'REF:PINNAME' → (gx, gy). Returns None if not found."""
    if ':' not in pin_str:
        return None
    ref, pin_ref = pin_str.split(':', 1)
    inst = instances_by_ref.get(ref)
    if not inst:
        return None
    lib_id = inst['lib_id']
    defn = local_definitions.get(lib_id)
    if not defn and lib_id and ':' in lib_id:
        lib_name, sym_name = lib_id.split(':', 1)
        defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
    if not defn:
        return None
    pin_data = find_pin_local_data(defn, pin_ref)
    if not pin_data:
        return None
    tx, ty, angle, mx, my = get_symbol_instance_transform(inst['sexpr'])
    gx, gy = transform_pin_coordinate(pin_data[0], pin_data[1], tx, ty, angle, mx, my)
    return gx, gy


def _load_instances(sch_sexpr):
    instances_by_ref = {}
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            lib_id_val = ref_val = None
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == 'lib_id':
                        lib_id_val = sub[1]
                    elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                        ref_val = sub[2]
            if ref_val:
                instances_by_ref[ref_val] = {'sexpr': child, 'lib_id': lib_id_val}
    return instances_by_ref


def annotate_schematic(schematic_path, table_path, annotations):
    """Add annotations to a KiCad schematic.

    Each annotation dict fields:
      type         : "global_label" | "power" | "no_connect" | "junction"
      pin          : "REF:PINNAME"  — resolve position from symbol pin (optional)
      x, y         : explicit coordinates (used when 'pin' absent)
      -- global_label only --
      name         : net label name
      shape        : input | output | bidirectional | passive | tri_state
      angle        : 0 | 90 | 180 | 270  (overrides 'side')
      side         : left | right | up | down  (converted to angle)
      stub         : stub wire length in mm (default 0 = label at pin, no wire)
      -- power only --
      name         : power net name (e.g. "GND", "+3V3")

    Returns total number of elements added.
    """
    project_dir = os.path.dirname(os.path.abspath(schematic_path))
    project_name = os.path.splitext(os.path.basename(schematic_path))[0]
    lib_map = load_sym_lib_table(table_path)

    with open(schematic_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)

    if not sch_sexpr or sch_sexpr[0] != 'kicad_sch':
        raise ValueError(f"Invalid KiCad schematic: {schematic_path}")

    sch_uuid = _get_sch_uuid(sch_sexpr)
    lib_syms = get_or_create_lib_symbols(sch_sexpr)

    local_definitions = {}
    for child in lib_syms[1:]:
        if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1:
            local_definitions[child[1]] = child

    instances_by_ref = _load_instances(sch_sexpr)

    added = 0

    for ann in annotations:
        ann_type = ann.get('type', '').lower()

        # Resolve position
        x, y = ann.get('x'), ann.get('y')
        if 'pin' in ann:
            pos = _resolve_pin_position(
                ann['pin'], instances_by_ref, local_definitions, lib_map, project_dir
            )
            if pos is None:
                print(f"Warning: Cannot resolve pin '{ann['pin']}'. Skipping.")
                continue
            x, y = pos

        if x is None or y is None:
            print(f"Warning: No position for annotation {ann}. Skipping.")
            continue

        x, y = float(x), float(y)

        if ann_type == 'global_label':
            name = ann.get('name')
            if not name:
                print(f"Warning: global_label missing 'name'. Skipping.")
                continue
            shape = ann.get('shape', 'passive')
            angle = ann.get('angle')
            if angle is None:
                side = ann.get('side', 'right')
                angle = _side_to_angle(side)
            angle = int(angle)
            stub = float(ann.get('stub', 0.0))

            lx, ly = x, y
            if stub > 0:
                offsets = {180: (-stub, 0), 0: (stub, 0), 90: (0, -stub), 270: (0, stub)}
                dx, dy = offsets.get(angle, (stub, 0))
                lx, ly = x + dx, y + dy
                sch_sexpr.append(make_wire_sexpr(x, y, lx, ly))
                added += 1

            sch_sexpr.append(make_global_label_sexpr(name, shape, lx, ly, angle))
            added += 1

        elif ann_type == 'power':
            name = ann.get('name', 'GND')
            pwr_ref = _next_pwr_ref(sch_sexpr)
            lib_id = f"power:{name}"
            if lib_id not in local_definitions:
                power_def = find_symbol_definition("power", name, lib_map, project_dir)
                if power_def:
                    power_def = list(power_def)
                    power_def[1] = lib_id
                    add_symbol_def_to_schematic(sch_sexpr, power_def)
                    local_definitions[lib_id] = power_def
                else:
                    print(f"Warning: power symbol '{name}' not found in library.")
            sch_sexpr.append(_make_power_instance(name, x, y, pwr_ref, sch_uuid, project_name))
            added += 1

        elif ann_type == 'no_connect':
            sch_sexpr.append(make_no_connect_sexpr(x, y))
            added += 1

        elif ann_type == 'junction':
            sch_sexpr.append(make_junction_sexpr(x, y))
            added += 1

        else:
            print(f"Warning: Unknown annotation type '{ann_type}'. Skipping.")

    sch_sexpr[1:] = dedupe_wire_children(sch_sexpr[1:])

    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))

    return added

