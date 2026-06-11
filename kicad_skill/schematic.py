import os
import math
import uuid
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
    # 1. Search in local libraries
    lib_path = lib_map.get(lib_name)
    if lib_path:
        if not os.path.isabs(lib_path):
            lib_path = os.path.abspath(os.path.join(project_dir, lib_path))
        if os.path.exists(lib_path):
            defn = parse_symbol_from_file(lib_path, sym_name)
            if defn:
                return defn
                
    # 2. Search in global KiCad installation path
    global_path = f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/{lib_name}.kicad_sym"
    if os.path.exists(global_path):
        defn = parse_symbol_from_file(global_path, sym_name)
        if defn:
            return defn
            
    return None


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
                    
        elif tag == 'pin':
            x, y, orientation = None, None, None
            length = None
            for p in node[1:]:
                if isinstance(p, list) and len(p) > 2 and p[0] == 'at':
                    x, y = float(p[1]), float(p[2])
                    if len(p) > 3:
                        orientation = float(p[3])
                    else:
                        orientation = 0.0
                elif isinstance(p, list) and len(p) > 1 and p[0] == 'length':
                    length = float(p[1])
            if x is not None:
                bbox.update_point(x, y)
                if length is not None and orientation is not None:
                    rad = math.radians(orientation)
                    dx = length * math.cos(rad)
                    dy = length * math.sin(rad)
                    bbox.update_point(x + dx, y + dy)
                    
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


def create_symbol_instance_sexpr(lib_id, reference, value, x, y, angle=0.0, properties_dict=None):
    """
    Creates an instance S-expression list for a symbol.
    """
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
            ["at", f"{(x + 2.54):.3f}", f"{(y - 1.27):.3f}", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left"]]
        ],
        ["property", "Value", value,
            ["at", f"{(x + 2.54):.3f}", f"{(y + 1.27):.3f}", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left"]]
        ]
    ]
    
    properties_dict = properties_dict or {}
    footprint = properties_dict.get("Footprint", "")
    datasheet = properties_dict.get("Datasheet", "")
    
    inst.append(
        ["property", "Footprint", footprint,
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]
        ]
    )
    inst.append(
        ["property", "Datasheet", datasheet,
            ["at", f"{x:.3f}", f"{y:.3f}", "0"],
            ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]
        ]
    )
    
    # Custom attributes
    for k, v in properties_dict.items():
        if k in ("Reference", "Value", "Footprint", "Datasheet"):
            continue
        inst.append(
            ["property", k, v,
                ["at", f"{x:.3f}", f"{y:.3f}", "0"],
                ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]
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


def place_symbols_and_resolve(schematic_path, table_path, new_placements, margin=2.54, resolve=True):
    """
    Places new symbols in the schematic, parses all symbols' bounds, resolves overlaps, and writes back.
    
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
        
        # If not defined locally in the schematic, look it up in external libraries
        if lib_id not in local_definitions:
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            if not defn:
                raise ValueError(f"Could not find symbol definition for {lib_id} in local table or global paths.")
            # Add definition to schematic lib_symbols
            add_symbol_def_to_schematic(sch_sexpr, defn)
            local_definitions[lib_id] = defn
            
    # 3. Create the new symbol instances in the schematic AST
    new_instances = []
    for placement in new_placements:
        inst = create_symbol_instance_sexpr(
            lib_id=placement['lib_id'],
            reference=placement['reference'],
            value=placement['value'],
            x=placement.get('x', 100.0),
            y=placement.get('y', 100.0),
            angle=placement.get('angle', 0.0),
            properties_dict=placement.get('properties')
        )
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
                tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(child)
                
                # Check if this instance is one of the newly added ones
                is_new = child in new_instances
                
                symbols_for_overlap.append({
                    'uuid': uuid_node,
                    'ref': ref_val or "Unknown",
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
    """
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
                add_symbol_def_to_schematic(sch_sexpr, defn1)
                local_definitions[inst1['lib_id']] = defn1
                
        defn2 = local_definitions.get(inst2['lib_id'])
        if not defn2 and inst2['lib_id'] and ':' in inst2['lib_id']:
            lib_name, sym_name = inst2['lib_id'].split(':', 1)
            defn2 = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
            if defn2:
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
        
        # Generate wire S-expression(s)
        orientation1 = (pin1_data[2] + angle1) % 360  # Global orientation of source pin
        
        if orthogonal:
            is_horizontal_pin = (orientation1 in (0.0, 180.0, 360.0))
            
            # Snap coordinates to grid to be safe
            grid = 1.27
            gx1, gy1 = round(gx1 / grid) * grid, round(gy1 / grid) * grid
            gx2, gy2 = round(gx2 / grid) * grid, round(gy2 / grid) * grid
            
            if is_horizontal_pin:
                # Segment 1: horizontal (x1, y1) -> (x2, y1)
                # Segment 2: vertical   (x2, y1) -> (x2, y2)
                if abs(gx1 - gx2) > 0.01:
                    new_wires.append(make_wire_sexpr(gx1, gy1, gx2, gy1))
                if abs(gy1 - gy2) > 0.01:
                    new_wires.append(make_wire_sexpr(gx2, gy1, gx2, gy2))
            else:
                # Segment 1: vertical   (x1, y1) -> (x1, y2)
                # Segment 2: horizontal (x1, y2) -> (x2, y2)
                if abs(gy1 - gy2) > 0.01:
                    new_wires.append(make_wire_sexpr(gx1, gy1, gx1, gy2))
                if abs(gx1 - gx2) > 0.01:
                    new_wires.append(make_wire_sexpr(gx1, gy2, gx2, gy2))
        else:
            # Straight diagonal line
            new_wires.append(make_wire_sexpr(gx1, gy1, gx2, gy2))
            
    # Add new wires to schematic AST
    for wire in new_wires:
        sch_sexpr.append(wire)
        
    # Save schematic
    with open(schematic_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))
        
    return len(new_wires)

