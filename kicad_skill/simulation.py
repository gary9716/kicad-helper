import os
import sys
import json
import re
import tempfile
import subprocess
from .parser import parse_sexpr, format_sexpr

EXPECTED_PORTS = {
    'opamp': ['inp', 'inn', 'out', 'vcc', 'vee'],
    'ldo_fixed': ['vin', 'vout', 'gnd'],
    'ldo_adj': ['vin', 'vout', 'gnd', 'fb'],
    'comparator': ['inp', 'inn', 'out', 'vcc', 'vee'],
    'vref': ['out', 'gnd']
}

DEFAULT_SPECS = {
    'opamp': {
        'gbw_hz': 1e6,
        'slew_vus': 1.0,
        'vos_mv': 0.0,
        'aol_db': 100.0,
        'rin_ohms': 1e12,
        'rro': False,
        'swing_v': 1.5
    },
    'ldo': {
        'vref': 0.8,
        'dropout_mv': 500.0,
        'iq_ua': 100.0,
        'fixed': False
    },
    'comparator': {
        'prop_delay_ns': 100.0,
        'output_type': 'push_pull'
    },
    'vref': {
        'vref': 2.5,
        'zout_ohms': 1.0,
        'iq_ua': 100.0
    }
}

def parse_eng_notation(val_str):
    if not isinstance(val_str, str):
        return val_str
    val_str = val_str.strip().lower()
    # Handle scientific notation like 1e6
    if 'e' in val_str:
        try:
            return float(val_str)
        except ValueError:
            pass
    # Match number followed by suffix
    m = re.match(r'^([\d.]+)\s*([a-zμ]*)$', val_str)
    if not m:
        try:
            return float(val_str)
        except ValueError:
            return val_str
    num_part, suffix = m.groups()
    num = float(num_part)
    
    si_suffixes = {
        't': 1e12,
        'g': 1e9,
        'meg': 1e6,
        'k': 1e3,
        'm': 1e-3,  # milli
        'u': 1e-6,
        'μ': 1e-6,
        'n': 1e-9,
        'p': 1e-12, # pico
        'f': 1e-15,
    }
    if suffix in si_suffixes:
        return num * si_suffixes[suffix]
    return num

def convert_params(params_dict, default_dict):
    converted = {}
    for k, v in default_dict.items():
        converted[k] = v
    for k, v in params_dict.items():
        if k in default_dict:
            expected_type = type(default_dict[k])
            if expected_type is bool:
                if isinstance(v, str):
                    converted[k] = v.lower().strip() in ('true', 'yes', '1')
                else:
                    converted[k] = bool(v)
            elif expected_type in (float, int):
                if isinstance(v, str):
                    converted[k] = parse_eng_notation(v)
                else:
                    converted[k] = float(v)
            else:
                converted[k] = str(v)
        else:
            converted[k] = v
    return converted

def find_spice_scripts_dir():
    paths_to_check = []
    if "SKILLS_DIR" in os.environ:
        paths_to_check.append(os.environ["SKILLS_DIR"])
    paths_to_check.extend([
        "/Users/gary/.gemini/skills",
        "/Users/gary/.gemini/antigravity-cli/skills"
    ])
    for base in paths_to_check:
        scripts_dir = os.path.join(base, "spice", "scripts")
        if os.path.isdir(scripts_dir):
            return scripts_dir
    return None

def update_subckt_in_library(file_path, subckt_name, subckt_content):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        content = ""

    pattern = re.compile(
        r'(^\s*\.subckt\s+' + re.escape(subckt_name) + r'\b.*?^\s*\.ends(?:\s+' + re.escape(subckt_name) + r'\b|\s+.*?)?)(?:\n|$)',
        re.IGNORECASE | re.DOTALL | re.MULTILINE
    )

    if pattern.search(content):
        new_content = pattern.sub(subckt_content + "\n", content)
    else:
        if content and not content.endswith('\n'):
            content += '\n'
        new_content = content + subckt_content + '\n'

    dir_name = os.path.dirname(file_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

def update_symbol_properties(symbol_sexpr, properties_dict):
    updated_keys = set()
    for child in symbol_sexpr[2:]:
        if isinstance(child, list) and child[0] == 'property':
            key = child[1]
            if key in properties_dict:
                child[2] = properties_dict[key]
                updated_keys.add(key)
                
    insert_idx = 2
    for idx, child in enumerate(symbol_sexpr):
        if isinstance(child, list) and child[0] == 'property':
            insert_idx = idx + 1
            
    for key, val in properties_dict.items():
        if key not in updated_keys:
            new_prop = [
                'property', key, val,
                ['at', '0', '0', '0'],
                ['effects', ['font', ['size', '1.27', '1.27']], ['hide', 'yes']]
            ]
            symbol_sexpr.insert(insert_idx, new_prop)
            insert_idx += 1

def handle_simulate(args):
    skills_dir = os.environ.get("SKILLS_DIR", "/Users/gary/.gemini/skills")
    analyze_script = os.path.join(skills_dir, "kicad", "scripts", "analyze_schematic.py")
    simulate_script = os.path.join(skills_dir, "spice", "scripts", "simulate_subcircuits.py")
    
    if not os.path.exists(analyze_script):
        print(f"Error: Schematic analyzer script not found at {analyze_script}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(simulate_script):
        print(f"Error: SPICE simulation script not found at {simulate_script}", file=sys.stderr)
        sys.exit(1)
        
    # Create a temp file for the schematic analysis JSON
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        temp_json_path = f.name
        
    try:
        print(f"Analyzing schematic '{args.schematic}'...", file=sys.stderr)
        cmd_analyze = [
            sys.executable,
            analyze_script,
            args.schematic,
            "--output", temp_json_path
        ]
        res = subprocess.run(cmd_analyze, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Error analyzing schematic:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
            
        print(f"Running SPICE simulation on analyzed schematic...", file=sys.stderr)
        cmd_sim = [
            sys.executable,
            simulate_script,
            temp_json_path
        ]
        if args.output:
            cmd_sim.extend(["--output", args.output])
        if args.workdir:
            cmd_sim.extend(["--workdir", args.workdir])
        if args.simulator:
            cmd_sim.extend(["--simulator", args.simulator])
        if args.types:
            cmd_sim.extend(["--types", args.types])
        if args.monte_carlo:
            cmd_sim.extend(["--monte-carlo", str(args.monte_carlo)])
        if args.mc_distribution:
            cmd_sim.extend(["--mc-distribution", args.mc_distribution])
        if args.mc_seed is not None:
            cmd_sim.extend(["--mc-seed", str(args.mc_seed)])
        if args.parasitics:
            cmd_sim.extend(["--parasitics", args.parasitics])
        if args.compact:
            cmd_sim.append("--compact")
            
        res_sim = subprocess.run(cmd_sim, capture_output=not args.output, text=True)
        if res_sim.returncode != 0:
            print(f"Error running simulation:\n{res_sim.stderr}", file=sys.stderr)
            sys.exit(1)
            
        if not args.output:
            print(res_sim.stdout)
            
    finally:
        if os.path.exists(temp_json_path):
            os.remove(temp_json_path)

def handle_add_spice_model(args):
    # Parse parameters
    params = {}
    if args.params:
        if args.params.strip().startswith('{'):
            try:
                params = json.loads(args.params)
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON parameters: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # key-value pairs
            for part in args.params.split(','):
                if not part.strip():
                    continue
                if '=' not in part:
                    print(f"Error: Invalid parameter format '{part}'. Use key=value.", file=sys.stderr)
                    sys.exit(1)
                k, v = part.split('=', 1)
                params[k.strip()] = v.strip()

    model_type = args.model_type.lower()
    if model_type not in DEFAULT_SPECS:
        print(f"Error: Invalid model type '{args.model_type}'. Must be one of: {', '.join(DEFAULT_SPECS.keys())}", file=sys.stderr)
        sys.exit(1)

    # Convert parameter types and fill defaults
    specs = convert_params(params, DEFAULT_SPECS[model_type])

    # Validate pin mapping
    mapping_dict = {}
    pin_mapping_str = args.pin_mapping.strip()
    parts = re.split(r'[\s,]+', pin_mapping_str)
    for part in parts:
        if not part:
            continue
        if '=' not in part:
            print(f"Error: Invalid pin mapping element '{part}'. Must be in format 'symbol_pin=model_pin'", file=sys.stderr)
            sys.exit(1)
        sym_pin, model_pin = part.split('=', 1)
        mapping_dict[sym_pin.strip()] = model_pin.strip()

    expected_type = model_type
    if model_type == 'ldo':
        if specs.get('fixed', False):
            expected_type = 'ldo_fixed'
        else:
            expected_type = 'ldo_adj'

    expected = EXPECTED_PORTS.get(expected_type)
    mapped_ports = set(mapping_dict.values())
    missing = set(expected) - mapped_ports
    if missing:
        print(f"Error: Missing pin mapping for required model ports: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Import spice_model_generator from spice skill
    spice_scripts_dir = find_spice_scripts_dir()
    if not spice_scripts_dir:
        print("Error: Could not locate spice skill scripts directory.", file=sys.stderr)
        sys.exit(1)

    if spice_scripts_dir not in sys.path:
        sys.path.insert(0, spice_scripts_dir)

    try:
        import spice_model_generator
    except ImportError as e:
        print(f"Error: Failed to import spice_model_generator from '{spice_scripts_dir}': {e}", file=sys.stderr)
        sys.exit(1)

    # Set up model name and file path
    mpn = args.model_name if args.model_name else args.symbol
    subckt_name = spice_model_generator.sanitize_mpn(mpn)
    
    if model_type == 'opamp':
        subckt_name = f"OPAMP_{subckt_name}"
        model_str = spice_model_generator.generate_opamp_model(mpn, specs)
    elif model_type == 'ldo':
        subckt_name = f"LDO_{subckt_name}"
        model_str = spice_model_generator.generate_ldo_model(mpn, specs)
    elif model_type == 'comparator':
        subckt_name = f"CMP_{subckt_name}"
        model_str = spice_model_generator.generate_comparator_model(mpn, specs)
    elif model_type == 'vref':
        subckt_name = f"VREF_{subckt_name}"
        model_str = spice_model_generator.generate_vref_model(mpn, specs)
    else:
        print(f"Error: Model type {model_type} not supported.", file=sys.stderr)
        sys.exit(1)

    # Resolve model file path
    lib_dir = os.path.dirname(os.path.abspath(args.library))
    if args.model_file:
        model_file_path = os.path.abspath(args.model_file)
    else:
        model_file_path = os.path.join(lib_dir, f"{args.symbol}.lib")

    # Update the .lib file with the generated model
    print(f"Generating SPICE model for '{args.symbol}' in '{model_file_path}'...", file=sys.stderr)
    update_subckt_in_library(model_file_path, subckt_name, model_str)

    # Determine Sim.Library value (relative to library folder if in the same folder)
    if os.path.dirname(model_file_path) == lib_dir:
        sim_library_val = os.path.basename(model_file_path)
    else:
        sim_library_val = model_file_path

    # Format the pin mapping string as space-separated key=value
    sim_pins_val = " ".join([f"{k}={v}" for k, v in mapping_dict.items()])

    # Properties to set on the KiCad symbol
    properties_to_set = {
        "Sim.Device": "SUBCKT",
        "Sim.Library": sim_library_val,
        "Sim.Name": subckt_name,
        "Sim.Pins": sim_pins_val
    }

    # Load and parse the symbol library
    if not os.path.exists(args.library):
        print(f"Error: Symbol library file '{args.library}' does not exist.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading symbol library '{args.library}'...", file=sys.stderr)
    try:
        with open(args.library, 'r', encoding='utf-8') as f:
            lib_content = f.read()
        lib_sexpr = parse_sexpr(lib_content)
    except Exception as e:
        print(f"Error parsing symbol library: {e}", file=sys.stderr)
        sys.exit(1)

    # Locate and update the symbol
    found = False
    for child in lib_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol' and len(child) > 1 and child[1] == args.symbol:
            update_symbol_properties(child, properties_to_set)
            found = True
            break

    if not found:
        print(f"Error: Symbol '{args.symbol}' not found in library '{args.library}'.", file=sys.stderr)
        sys.exit(1)

    # Write back the updated library S-expression
    print(f"Updating symbol properties and saving library...", file=sys.stderr)
    try:
        with open(args.library, 'w', encoding='utf-8') as f:
            f.write(format_sexpr(lib_sexpr))
    except Exception as e:
        print(f"Error saving symbol library: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Successfully attached SPICE model '{subckt_name}' to symbol '{args.symbol}' in library '{args.library}'")
