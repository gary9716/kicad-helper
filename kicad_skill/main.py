import argparse
import json
import os
import sys
from .symbol import generate_symbol_sexpr, save_symbol_to_library
from .schematic import place_symbols_and_resolve, connect_symbols_in_schematic, annotate_schematic

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
        height=args.height,
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


def handle_simplify_wires(args):
    table_path = args.table
    if not table_path:
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")
    from .wire_complexity import simplify_wires
    weights = {"crossings": args.wc, "bends": args.wb, "length": args.wl}
    try:
        res = simplify_wires(
            sch_path=args.schematic, table_path=table_path,
            threshold=args.threshold, weights=weights,
            max_conversions=args.max_conversions, dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error simplifying wires: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Total complexity: {res['total_before']:.1f} -> {res['total_after']:.1f}"
          + (" (dry-run)" if args.dry_run else ""))
    for c in res["converted"]:
        print(f"  CONVERTED {c['pin_a']} <-> {c['pin_b']} as label '{c['net_name']}' (score {c['score']:.1f})")
    for s in res["skipped_unsafe"]:
        print(f"  SKIPPED   {s['pin_a']} <-> {s['pin_b']}: {s['reason']}")


def handle_create_module(args):
    # Auto-detect table path if not specified
    table_path = args.table
    if not table_path:
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")
        if not os.path.exists(table_path):
            print(f"Warning: sym-lib-table not found at '{table_path}'. Custom libraries might fail to load.")

    components = [c.strip() for c in args.components.split(',') if c.strip()]
    if not components:
        print("Error: --components must not be empty.", file=sys.stderr)
        sys.exit(1)

    print(f"Creating module '{args.name}' from components: {', '.join(components)}...")
    try:
        from .module import create_module_from_components
        num_pins, num_wires = create_module_from_components(
            schematic_path=args.schematic,
            table_path=table_path,
            components=components,
            module_name=args.name,
            sheet_file_name=args.sheet_file
        )
        print(f"Successfully created sub-sheet '{args.sheet_file}' with {num_pins} hierarchical pin(s) and routed {num_wires} connection wire(s) in parent sheet.")
    except Exception as e:
        print(f"Error creating module: {e}", file=sys.stderr)
        sys.exit(1)

def handle_annotate(args):
    annotations = []
    if args.annotations_json:
        if os.path.exists(args.annotations_json):
            with open(args.annotations_json, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
        else:
            annotations = json.loads(args.annotations_json)
    else:
        print("Error: --annotations-json is required.", file=sys.stderr)
        sys.exit(1)

    table_path = args.table
    if not table_path:
        table_path = os.path.join(os.path.dirname(os.path.abspath(args.schematic)), "sym-lib-table")

    try:
        n = annotate_schematic(
            schematic_path=args.schematic,
            table_path=table_path,
            annotations=annotations,
        )
        print(f"Added {n} annotation element(s) to '{args.schematic}'.")
    except Exception as e:
        print(f"Error annotating schematic: {e}", file=sys.stderr)
        sys.exit(1)


def handle_snapshot(args):
    import subprocess, shutil
    KICAD_CLI = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
    if not os.path.exists(KICAD_CLI):
        # Try PATH
        KICAD_CLI = shutil.which('kicad-cli') or ''
    if not KICAD_CLI:
        print("Error: kicad-cli not found. Install KiCad 7+.", file=sys.stderr)
        sys.exit(1)

    sch = os.path.abspath(args.schematic)
    if not os.path.exists(sch):
        print(f"Error: schematic not found: {sch}", file=sys.stderr)
        sys.exit(1)

    fmt = args.fmt
    if args.output:
        out = os.path.abspath(args.output)
    else:
        base = os.path.splitext(sch)[0]
        out = base + '.' + fmt

    out_dir = os.path.dirname(out)
    out_name = os.path.basename(out)

    cmd = [KICAD_CLI, 'sch', 'export', fmt,
           '--output', out_dir,
           sch]
    if args.theme:
        cmd += ['--theme', args.theme]
    if args.black_and_white:
        cmd.append('--black-and-white')
    if args.pages:
        cmd += ['--pages', args.pages]

    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("Error: kicad-cli timed out after 60s", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"kicad-cli failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    # kicad-cli names the file after the schematic; rename if output path differs
    sch_stem = os.path.splitext(os.path.basename(sch))[0]
    generated = os.path.join(out_dir, sch_stem + '.' + fmt)
    if generated != out and os.path.exists(generated):
        os.rename(generated, out)
        generated = out

    if os.path.exists(generated):
        size = os.path.getsize(generated)
        print(f"Snapshot written: {generated} ({size} bytes)")
    else:
        # SVG might be in a subdir
        import glob
        found = glob.glob(os.path.join(out_dir, '**', '*.' + fmt), recursive=True)
        if found:
            print(f"Snapshot written: {found[0]}")
        else:
            print(f"Warning: output file not found after export. kicad-cli stdout:\n{result.stdout}")


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
    sym_parser.add_argument("--height", type=float, help="Height of the symbol body in mm")
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
    
    # simulate parser
    sim_parser = subparsers.add_parser("simulate", help="Run SPICE simulation on a KiCad schematic")
    sim_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    sim_parser.add_argument("--output", help="Path to the output simulation JSON report")
    sim_parser.add_argument("--workdir", help="Directory for temporary simulation files (.cir, .log)")
    sim_parser.add_argument("--simulator", choices=["ngspice", "ltspice", "xyce"], help="Force specific SPICE simulator")
    sim_parser.add_argument("--types", help="Comma-separated list of subcircuit types to simulate")
    sim_parser.add_argument("--monte-carlo", type=int, default=0, help="Number of Monte Carlo tolerance trials (0 to disable)")
    sim_parser.add_argument("--mc-distribution", choices=["gaussian", "uniform"], default="gaussian", help="Monte Carlo distribution (default: gaussian)")
    sim_parser.add_argument("--mc-seed", type=int, default=42, help="Random seed for Monte Carlo runs (default: 42)")
    sim_parser.add_argument("--parasitics", help="Path to PCB parasitic JSON from extract_parasitics.py")
    sim_parser.add_argument("--compact", action="store_true", help="Compact output (omit file paths)")

    # add-spice-model parser
    model_parser = subparsers.add_parser("add-spice-model", help="Generate and attach a SPICE model to a custom symbol")
    model_parser.add_argument("--library", required=True, help="Path to the .kicad_sym library file")
    model_parser.add_argument("--symbol", required=True, help="Name of the symbol in the library")
    model_parser.add_argument("--model-type", required=True, choices=["opamp", "ldo", "comparator", "vref"], help="Type of behavioral model to build")
    model_parser.add_argument("--pin-mapping", required=True, help="Pin mapping shorthand, e.g. '1=inp,2=inn,3=out,4=vcc,5=vee'")
    model_parser.add_argument("--model-file", help="Path to output SPICE .lib file (default: same folder/name as symbol)")
    model_parser.add_argument("--params", help="Key-value parameters or JSON string for the model specs")
    model_parser.add_argument("--model-name", help="Custom name for the subcircuit model (default: matches symbol name)")
    
    # create-module parser
    module_parser = subparsers.add_parser("create-module", help="Create a sub-sheet from a group of components and connect boundary crossings")
    module_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    module_parser.add_argument("--components", required=True, help="Comma-separated list of component references to move (e.g. U101,U102,R101)")
    module_parser.add_argument("--name", required=True, help="Name of the sub-sheet module")
    module_parser.add_argument("--sheet-file", required=True, help="Filename of the sub-sheet schematic, e.g. custom_sheet.kicad_sch")
    module_parser.add_argument("--table", help="Path to the sym-lib-table file (default: same folder as schematic)")

    # simplify-wires parser
    simp_parser = subparsers.add_parser("simplify-wires", help="Convert high-complexity wires to local labels")
    simp_parser.add_argument("--schematic", required=True, help="Path to the .kicad_sch file")
    simp_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")
    simp_parser.add_argument("--threshold", type=float, default=50.0, help="Total complexity target (default: 50)")
    simp_parser.add_argument("--max", type=int, default=None, dest="max_conversions", help="Max conversions")
    simp_parser.add_argument("--wc", type=float, default=10.0, help="Crossing weight (default: 10)")
    simp_parser.add_argument("--wb", type=float, default=2.0, help="Bend weight (default: 2)")
    simp_parser.add_argument("--wl", type=float, default=0.5, help="Length weight (default: 0.5)")
    simp_parser.add_argument("--dry-run", action="store_true", help="Report plan without writing")

    # annotate parser
    ann_parser = subparsers.add_parser("annotate", help="Add global labels, power symbols, no_connects, and junctions to a schematic")
    ann_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    ann_parser.add_argument("--annotations-json", required=True, help="JSON array of annotation objects, or path to a JSON file")
    ann_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")

    # snapshot parser
    snap_parser = subparsers.add_parser("snapshot", help="Export schematic as SVG/PDF ground-truth snapshot via kicad-cli")
    snap_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    snap_parser.add_argument("--output", help="Output file path (default: <schematic>.svg next to schematic)")
    snap_parser.add_argument("--format", choices=["svg", "pdf"], default="svg", dest="fmt", help="Export format (default: svg)")
    snap_parser.add_argument("--theme", default="", help="KiCad color theme name (default: schematic default)")
    snap_parser.add_argument("--black-and-white", action="store_true", help="Monochrome output")
    snap_parser.add_argument("--pages", default="", help="Comma-separated page numbers (default: all)")

    # resolve parser
    resolve_parser = subparsers.add_parser("resolve", help="Two-pass AABB layout resolver: intra-cluster then inter-cluster rigid-body")
    resolve_parser.add_argument("--schematic", required=True, help="Path to the KiCad schematic (.kicad_sch) file")
    resolve_parser.add_argument("--table", help="Path to sym-lib-table (default: same folder as schematic)")
    resolve_parser.add_argument("--output", help="Output schematic path (default: overwrite input)")
    resolve_parser.add_argument("--max-iter", type=int, default=50, help="Max MTV iterations per pass (default: 50)")
    resolve_parser.add_argument("--grid", type=float, default=2.54, help="Grid snap in mm (default: 2.54)")
    resolve_parser.add_argument("--dry-run", action="store_true", help="Compute moves but do not write output")

    # import-lib parser
    import_lib_parser = subparsers.add_parser("import-lib", help="Import a KiCad v6+ Ultra Librarian component into local library")
    import_lib_parser.add_argument("source_path", help="Path to the Ultra Librarian download folder")
    import_lib_parser.add_argument("--lib-root", default="~/hardwares/Libraries", help="Root directory for installed libraries (default: ~/hardwares/Libraries)")
    import_lib_parser.add_argument("--project", default=None, help="Path to .kicad_pro for project-level registration (default: global)")
    import_lib_parser.add_argument("--force", action="store_true", help="Overwrite if component already exists in lib-root")

    args = parser.parse_args()

    if args.command == "create-symbol":
        handle_create_symbol(args)
    elif args.command == "place":
        handle_place(args)
    elif args.command == "connect":
        handle_connect(args)
    elif args.command == "simulate":
        from .simulation import handle_simulate
        handle_simulate(args)
    elif args.command == "add-spice-model":
        from .simulation import handle_add_spice_model
        handle_add_spice_model(args)
    elif args.command == "create-module":
        handle_create_module(args)
    elif args.command == "simplify-wires":
        handle_simplify_wires(args)
    elif args.command == 'annotate':
        handle_annotate(args)
    elif args.command == 'snapshot':
        handle_snapshot(args)
    elif args.command == 'resolve':
        from .resolve_layout import resolve_schematic_layout
        import os
        table = args.table or os.path.join(os.path.dirname(args.schematic), 'sym-lib-table')
        res = resolve_schematic_layout(
            args.schematic, table,
            max_iter=args.max_iter,
            grid=args.grid,
            dry_run=args.dry_run,
            out_path=args.output,
        )
        print(f"Clusters:     {res['clusters']}")
        print(f"Pass-1 moves: {res['pass1_moves']}  (intra-cluster)")
        print(f"Pass-2 moves: {res['pass2_moves']}  (inter-cluster rigid body)")
        print(f"Remaining:    {res['remaining']}  symbol-symbol overlaps")
        if res['remaining'] == 0:
            print("OK — no overlaps remaining.")
        else:
            print(f"WARNING: {res['remaining']} overlap(s) unresolved — try --max-iter or check space.")
    elif args.command == 'import-lib':
        from .import_lib import handle_import_lib
        handle_import_lib(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
