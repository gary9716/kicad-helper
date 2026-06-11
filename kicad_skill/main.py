import argparse
import json
import os
import sys
from .symbol import generate_symbol_sexpr, save_symbol_to_library
from .schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def parse_pins_shorthand(shorthand_str):
    """
    Parses pin specification shorthand in the format:
    side:number:name:type,side:number:name:type,...
    e.g., left:1:VCC:power_in,left:2:PA0:bidirectional,right:3:GND:power_in
    """
    pins = []
    parts = shorthand_str.split(',')
    for part in parts:
        if not part.strip():
            continue
        subparts = part.split(':')
        if len(subparts) < 3:
            raise ValueError(f"Invalid pin shorthand element '{part}'. Must be side:number:name[:type]")
        
        side = subparts[0].strip().lower()
        if side not in ('left', 'right', 'top', 'bottom'):
            raise ValueError(f"Invalid pin side '{side}'. Must be left, right, top, or bottom.")
            
        number = subparts[1].strip()
        name = subparts[2].strip()
        pin_type = subparts[3].strip() if len(subparts) > 3 else "unspecified"
        
        pins.append({
            "side": side,
            "number": number,
            "name": name,
            "type": pin_type
        })
    return pins


def parse_connections_shorthand(shorthand_str):
    """
    Parses connection shorthand in format: Ref1:Pin1 to Ref2:Pin2, Ref3:Pin3-Ref4:Pin4
    """
    connections = []
    parts = shorthand_str.split(',')
    for part in parts:
        if not part.strip():
            continue
            
        delim = None
        for d in (' to ', '->', '-'):
            if d in part:
                delim = d
                break
        if not delim:
            raise ValueError(f"Invalid connection format '{part}'. Must be 'Ref1:Pin1 to Ref2:Pin2' (or '-' or '->')")
            
        src, dest = part.split(delim, 1)
        connections.append({
            "from": src.strip(),
            "to": dest.strip()
        })
    return connections


def handle_connect(args):
    # Parse connections
    connections = []
    if args.connections_json:
        if os.path.exists(args.connections_json):
            with open(args.connections_json, 'r', encoding='utf-8') as f:
                connections = json.load(f)
        else:
            connections = json.loads(args.connections_json)
    elif args.connections:
        connections = parse_connections_shorthand(args.connections)
    else:
        print("Error: Either --connections or --connections-json must be provided.", file=sys.stderr)
        sys.exit(1)
        
    table_path = args.table
    if not table_path:
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")
        
    print(f"Connecting symbols in schematic '{args.schematic}'...")
    try:
        num_wires = connect_symbols_in_schematic(
            schematic_path=args.schematic,
            table_path=table_path,
            connections=connections,
            orthogonal=not args.diagonal
        )
        print(f"Successfully added {num_wires} wire connection(s) to schematic.")
    except Exception as e:
        print(f"Error connecting symbols: {e}", file=sys.stderr)
        sys.exit(1)


def handle_create_symbol(args):
    # Determine pins list
    pins = []
    if args.pins_json:
        if os.path.exists(args.pins_json):
            with open(args.pins_json, 'r', encoding='utf-8') as f:
                pins = json.load(f)
        else:
            # Try parsing it directly as a JSON string
            pins = json.loads(args.pins_json)
    elif args.pins:
        pins = parse_pins_shorthand(args.pins)
    else:
        print("Error: Either --pins or --pins-json must be provided.", file=sys.stderr)
        sys.exit(1)
        
    symbol_def = generate_symbol_sexpr(
        name=args.name,
        pins=pins,
        ref_prefix=args.ref_prefix,
        width=args.width,
        pin_length=args.pin_length
    )
    
    save_symbol_to_library(args.library, symbol_def)
    print(f"Successfully generated symbol '{args.name}' and saved to library '{args.library}'")

def handle_place(args):
    # Parse placements JSON
    placements = []
    if os.path.exists(args.placements):
        with open(args.placements, 'r', encoding='utf-8') as f:
            placements = json.load(f)
    else:
        try:
            placements = json.loads(args.placements)
        except json.JSONDecodeError as e:
            print(f"Error parsing placements JSON: {e}", file=sys.stderr)
            sys.exit(1)
            
    # Auto-detect table path if not specified
    table_path = args.table
    if not table_path:
        # Default to sym-lib-table in the same folder as schematic
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")
        if not os.path.exists(table_path):
            print(f"Warning: sym-lib-table not found at '{table_path}'. Custom libraries might fail to load.")
            
    print(f"Placing {len(placements)} symbols in schematic '{args.schematic}'...")
    
    try:
        resolved_symbols = place_symbols_and_resolve(
            schematic_path=args.schematic,
            table_path=table_path,
            new_placements=placements,
            margin=args.margin,
            resolve=not args.no_resolve
        )
        
        print("\nPlaced Symbols Summary:")
        for sym in resolved_symbols:
            status = "Moved/Resolved" if sym['movable'] else "Fixed"
            print(f"  [{status}] {sym['ref']} ({sym['sexpr'][1][1]}): at ({sym['tx']:.2f}, {sym['ty']:.2f})")
            
        print(f"\nSuccessfully updated schematic '{args.schematic}'")
    except Exception as e:
        print(f"Error placing symbols: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="KiCad Helper: Symbol Creator and Collision-Free Placement Tool")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")
    
    # create-symbol parser
    sym_parser = subparsers.add_parser("create-symbol", help="Create a custom KiCad symbol and save to a .kicad_sym library")
    sym_parser.add_argument("--name", required=True, help="Name of the symbol")
    sym_parser.add_argument("--library", required=True, help="Path to the output .kicad_sym library file")
    sym_parser.add_argument("--pins", help="Pin layout shorthand, e.g., left:1:VCC:power_in,left:2:PA0:bidirectional,right:3:GND")
    sym_parser.add_argument("--pins-json", help="Path to a JSON file (or a JSON string) defining the pins")
    sym_parser.add_argument("--ref-prefix", default="U", help="Reference designator prefix (default: U)")
    sym_parser.add_argument("--width", type=float, default=10.16, help="Width of the symbol body in mm (default: 10.16)")
    sym_parser.add_argument("--pin-length", type=float, default=2.54, help="Pin length in mm (default: 2.54)")
    
    # place parser
    place_parser = subparsers.add_parser("place", help="Place symbol instances into a schematic and resolve overlaps")
    place_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    place_parser.add_argument("--placements", required=True, help="JSON list of symbol placements (or path to a JSON file)")
    place_parser.add_argument("--table", help="Path to the sym-lib-table file (default: same folder as schematic)")
    place_parser.add_argument("--margin", type=float, default=2.54, help="Bounding box padding/margin in mm for overlap detection (default: 2.54)")
    place_parser.add_argument("--no-resolve", action="store_true", help="Disable overlap/collision resolution")
    
    # connect parser
    connect_parser = subparsers.add_parser("connect", help="Connect symbol pins with wires in a schematic")
    connect_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    connect_parser.add_argument("--connections", help="Connection shorthand, e.g., U101:PA0 to U102:1, U101:PA1-U103:2")
    connect_parser.add_argument("--connections-json", help="Path to a JSON file (or a JSON string) defining the connections")
    connect_parser.add_argument("--table", help="Path to the sym-lib-table file (default: same folder as schematic)")
    connect_parser.add_argument("--diagonal", action="store_true", help="Use straight diagonal wires instead of L-shaped orthogonal lines")
    
    args = parser.parse_args()
    
    if args.command == "create-symbol":
        handle_create_symbol(args)
    elif args.command == "place":
        handle_place(args)
    elif args.command == "connect":
        handle_connect(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
