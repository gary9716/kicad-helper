import os
from .parser import parse_sexpr, format_sexpr

def generate_symbol_sexpr(name, pins, ref_prefix="U", width=10.16, height=None, pin_length=2.54):
    """
    Generates a list representing a symbol's KiCad S-expression.
    Pins is a list of dictionaries, e.g.:
    [
        {"name": "PA0", "number": "1", "type": "bidirectional", "side": "left"},
        ...
    ]
    Sides: 'left', 'right', 'top', 'bottom'.
    """
    # Validate pin definitions
    VALID_PIN_TYPES = {
        "input", "output", "bidirectional", "tri_state", "passive",
        "free", "unspecified", "power_in", "power_out", 
        "open_collector", "open_emitter"
    }
    
    for p in pins:
        pin_name = p.get("name")
        pin_num = p.get("number")
        pin_type = p.get("type", "unspecified")
        
        if not pin_name or not pin_num:
            raise ValueError(f"Pin definitions must contain both 'name' and 'number'. Got: {p}")
            
        if pin_type not in VALID_PIN_TYPES:
            raise ValueError(
                f"Invalid pin type '{pin_type}' for pin '{pin_name}'. "
                f"Must be one of: {', '.join(sorted(VALID_PIN_TYPES))}"
            )
            
        # Check for MISO shared bus conflicts
        if "MISO" in pin_name.upper() and pin_type == "output":
            raise ValueError(
                f"Pin '{pin_name}' (number {pin_num}) has type 'output'. "
                f"For SPI MISO pins on shared buses, you must use 'tri_state' (or 'passive') "
                f"to prevent KiCad ERC conflicts."
            )

    # Group pins by side
    left_pins = [p for p in pins if p.get("side", "left") == "left"]
    right_pins = [p for p in pins if p.get("side", "left") == "right"]
    top_pins = [p for p in pins if p.get("side", "left") == "top"]
    bottom_pins = [p for p in pins if p.get("side", "left") == "bottom"]
    
    # Group pins by side
    # Compute body dimensions
    spacing = 2.54
    max_side_pins = max(len(left_pins), len(right_pins))
    max_tb_pins = max(len(top_pins), len(bottom_pins))
    
    height_pins = max(max_side_pins, 1)
    body_height = (height_pins + 1) * spacing
    if height is not None:
        body_height = max(height, body_height)
    
    width_pins = max(max_tb_pins, 1)
    body_width = max(width, (width_pins + 1) * spacing)
    
    # Adjust for pin name overlaps
    import math
    char_width = 1.0  # safe estimate for character width at 1.27 size
    gap = 2.54
    
    max_left_len = max([len(p["name"]) for p in left_pins] + [0])
    max_right_len = max([len(p["name"]) for p in right_pins] + [0])
    min_width_names = (max_left_len + max_right_len) * char_width + gap
    min_width_names = math.ceil(min_width_names / spacing) * spacing
    body_width = max(body_width, min_width_names)
    
    max_top_len = max([len(p["name"]) for p in top_pins] + [0])
    max_bottom_len = max([len(p["name"]) for p in bottom_pins] + [0])
    min_height_names = (max_top_len + max_bottom_len) * char_width + gap
    min_height_names = math.ceil(min_height_names / spacing) * spacing
    body_height = max(body_height, min_height_names)
    
    # Center-align body
    left_x = -body_width / 2
    right_x = body_width / 2
    top_y = -body_height / 2
    bottom_y = body_height / 2
    
    # Round to KiCad grid (2.54mm / 0.1in)
    left_x = round(left_x / spacing) * spacing
    right_x = round(right_x / spacing) * spacing
    top_y = round(top_y / spacing) * spacing
    bottom_y = round(bottom_y / spacing) * spacing
    
    # Construct standard fields
    properties = [
        ["property", "Reference", ref_prefix, 
            ["at", f"{left_x:.2f}", f"{(top_y - 2.54):.2f}", "0"], 
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left"]]
        ],
        ["property", "Value", name, 
            ["at", f"{left_x:.2f}", f"{(bottom_y + 2.54):.2f}", "0"], 
            ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left"]]
        ],
        ["property", "Footprint", "", 
            ["at", "0", "0", "0"], 
            ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]
        ],
        ["property", "Datasheet", "", 
            ["at", "0", "0", "0"], 
            ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]
        ],
    ]
    
    # Symbol Body (Unit 0, Style 1)
    body_symbol = [
        f"symbol", f"{name}_0_1",
        ["rectangle", 
            ["start", f"{left_x:.2f}", f"{top_y:.2f}"], 
            ["end", f"{right_x:.2f}", f"{bottom_y:.2f}"],
            ["stroke", ["width", "0.254"], ["type", "default"]],
            ["fill", ["type", "background"]]
        ]
    ]
    
    # Symbol Pins (Unit 1, Style 1)
    pins_symbol = ["symbol", f"{name}_1_1"]
    
    # Left pins: orientation 0 (points right, from connection point to body)
    left_start_y = top_y + (body_height - (len(left_pins) - 1) * spacing) / 2
    left_start_y = round(left_start_y / spacing) * spacing
    for idx, pin in enumerate(left_pins):
        py = left_start_y + idx * spacing
        px = left_x - pin_length
        pin_expr = [
            "pin", pin.get("type", "unspecified"), "line",
            ["at", f"{px:.2f}", f"{py:.2f}", "0"],
            ["length", f"{pin_length:.2f}"],
            ["name", pin["name"], ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ["number", pin["number"], ["effects", ["font", ["size", "1.27", "1.27"]]]]
        ]
        pins_symbol.append(pin_expr)
        
    # Right pins: orientation 180 (points left)
    right_start_y = top_y + (body_height - (len(right_pins) - 1) * spacing) / 2
    right_start_y = round(right_start_y / spacing) * spacing
    for idx, pin in enumerate(right_pins):
        py = right_start_y + idx * spacing
        px = right_x + pin_length
        pin_expr = [
            "pin", pin.get("type", "unspecified"), "line",
            ["at", f"{px:.2f}", f"{py:.2f}", "180"],
            ["length", f"{pin_length:.2f}"],
            ["name", pin["name"], ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ["number", pin["number"], ["effects", ["font", ["size", "1.27", "1.27"]]]]
        ]
        pins_symbol.append(pin_expr)
        
    # Top pins: orientation 270 (points down, wait! from connection point to body is pointing down: +Y is down, so orientation 90)
    top_start_x = left_x + (body_width - (len(top_pins) - 1) * spacing) / 2
    top_start_x = round(top_start_x / spacing) * spacing
    for idx, pin in enumerate(top_pins):
        px = top_start_x + idx * spacing
        py = top_y - pin_length
        pin_expr = [
            "pin", pin.get("type", "unspecified"), "line",
            ["at", f"{px:.2f}", f"{py:.2f}", "90"],
            ["length", f"{pin_length:.2f}"],
            ["name", pin["name"], ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ["number", pin["number"], ["effects", ["font", ["size", "1.27", "1.27"]]]]
        ]
        pins_symbol.append(pin_expr)
        
    # Bottom pins: orientation 270 (points up, from connection point to body is pointing up: -Y is up, so orientation 270)
    bottom_start_x = left_x + (body_width - (len(bottom_pins) - 1) * spacing) / 2
    bottom_start_x = round(bottom_start_x / spacing) * spacing
    for idx, pin in enumerate(bottom_pins):
        px = bottom_start_x + idx * spacing
        py = bottom_y + pin_length
        pin_expr = [
            "pin", pin.get("type", "unspecified"), "line",
            ["at", f"{px:.2f}", f"{py:.2f}", "270"],
            ["length", f"{pin_length:.2f}"],
            ["name", pin["name"], ["effects", ["font", ["size", "1.27", "1.27"]]]],
            ["number", pin["number"], ["effects", ["font", ["size", "1.27", "1.27"]]]]
        ]
        pins_symbol.append(pin_expr)
        
    # Full symbol definition
    symbol_def = [
        "symbol", name,
        ["pin_names", ["offset", "1.016"]],
        ["in_bom", "yes"],
        ["on_board", "yes"]
    ]
    symbol_def.extend(properties)
    symbol_def.append(body_symbol)
    symbol_def.append(pins_symbol)
    
    return symbol_def

def save_symbol_to_library(library_path, symbol_def):
    """
    Saves or updates a symbol definition inside a .kicad_sym file.
    """
    symbol_name = symbol_def[1]
    
    if os.path.exists(library_path):
        with open(library_path, 'r', encoding='utf-8') as f:
            content = f.read()
        try:
            lib_sexpr = parse_sexpr(content)
        except Exception as e:
            raise ValueError(f"Failed to parse existing library {library_path}: {e}")
            
        if not lib_sexpr or lib_sexpr[0] != 'kicad_symbol_lib':
            raise ValueError(f"Invalid symbol library root node in {library_path}")
            
        # Look for existing symbol and remove it
        new_children = []
        for child in lib_sexpr[1:]:
            if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1 and child[1] == symbol_name:
                continue
            new_children.append(child)
            
        # Append the new symbol
        new_children.append(symbol_def)
        
        lib_sexpr = ['kicad_symbol_lib'] + new_children
    else:
        # Create a new library file
        lib_sexpr = [
            'kicad_symbol_lib',
            ['version', '20200827'],
            ['generator', 'kicad_symbol_editor'],
            symbol_def
        ]
        
    # Save back
    dir_name = os.path.dirname(library_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name)
        
    with open(library_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(lib_sexpr))
