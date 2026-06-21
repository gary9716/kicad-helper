import os
import shutil
import glob


def _inject_lib_entry(content: str, name: str, new_entry: str):
    """Inject (lib ...) entry before closing paren. Returns None if name already present."""
    if f'(name "{name}")' in content:
        return None
    return content.rstrip().rstrip(')') + '\n' + new_entry + '\n)'


def validate_source(source_path: str) -> dict:
    """Check source has KiCADv6/*.kicad_sym and KiCADv6/footprints.pretty/."""
    kicad_dir = os.path.join(source_path, 'KiCADv6')
    if not os.path.isdir(kicad_dir):
        raise ValueError(f"No KiCADv6/ directory found in {source_path}")

    sym_files = glob.glob(os.path.join(kicad_dir, '*.kicad_sym'))
    if not sym_files:
        raise ValueError(f"No .kicad_sym file found in {kicad_dir}")

    fp_dir = os.path.join(kicad_dir, 'footprints.pretty')
    if not os.path.isdir(fp_dir):
        raise ValueError(f"No footprints.pretty/ directory found in {kicad_dir}")

    return {'sym_path': sym_files[0], 'fp_dir': fp_dir}


def copy_component(source_path: str, lib_root: str, component_name: str, force: bool = False) -> dict:
    """Copy KiCADv6/ tree to <lib_root>/<component_name>/KiCADv6/."""
    dest = os.path.join(os.path.expanduser(lib_root), component_name, 'KiCADv6')
    if os.path.exists(dest):
        if not force:
            raise FileExistsError(f"{dest} already exists — use --force to overwrite")
        shutil.rmtree(dest)

    shutil.copytree(os.path.join(source_path, 'KiCADv6'), dest)

    sym_files = glob.glob(os.path.join(dest, '*.kicad_sym'))
    return {'dest_sym': sym_files[0], 'dest_fp_dir': os.path.join(dest, 'footprints.pretty')}


_MINIMAL_SYM_TABLE = '(sym_lib_table\n  (version 7)\n)'
_MINIMAL_FP_TABLE = '(fp_lib_table\n  (version 7)\n)'


def register_symbol(table_dir: str, name: str, sym_uri: str) -> bool:
    """Add symbol lib entry to sym-lib-table. Returns True if added, False if already present."""
    table_path = os.path.join(table_dir, 'sym-lib-table')
    if os.path.exists(table_path):
        with open(table_path) as f:
            content = f.read()
    else:
        content = _MINIMAL_SYM_TABLE
    entry = f'  (lib (name "{name}") (type "KiCad") (uri "{sym_uri}") (options "") (descr ""))'
    result = _inject_lib_entry(content, name, entry)
    if result is None:
        return False
    with open(table_path, 'w') as f:
        f.write(result)
    return True


def register_footprint(table_dir: str, name: str, fp_uri: str) -> bool:
    """Add footprint lib entry to fp-lib-table. Returns True if added, False if already present."""
    table_path = os.path.join(table_dir, 'fp-lib-table')
    if os.path.exists(table_path):
        with open(table_path) as f:
            content = f.read()
    else:
        content = _MINIMAL_FP_TABLE
    entry = f'  (lib (name "{name}") (type "KiCad") (uri "{fp_uri}") (options "") (descr ""))'
    result = _inject_lib_entry(content, name, entry)
    if result is None:
        return False
    with open(table_path, 'w') as f:
        f.write(result)
    return True


_DEFAULT_KICAD_PREFS = os.path.expanduser('~/Library/Preferences/kicad')


def _find_global_table_dir(base: str = _DEFAULT_KICAD_PREFS) -> str:
    """Return path to latest KiCad version config dir."""
    if not os.path.isdir(base):
        raise FileNotFoundError(f"KiCad config not found at {base}")
    version_dirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    if not version_dirs:
        raise FileNotFoundError(f"No KiCad version dirs found in {base}")
    # Sort by semantic version (e.g., "9.0" < "10.0")
    versions = sorted(version_dirs, key=lambda v: tuple(map(int, v.split('.'))))
    return os.path.join(base, versions[-1])


def handle_import_lib(args):
    source_path = os.path.expanduser(args.source_path)
    lib_root = os.path.expanduser(args.lib_root)
    component_name = os.path.basename(source_path.rstrip('/\\'))

    validate_source(source_path)

    print(f"Copying {component_name} → {os.path.join(lib_root, component_name)}/KiCADv6/")
    paths = copy_component(source_path, lib_root, component_name, force=args.force)

    fp_count = len(glob.glob(os.path.join(paths['dest_fp_dir'], '*.kicad_mod')))
    print(f"  symbol:    {os.path.basename(paths['dest_sym'])}")
    print(f"  footprint: footprints.pretty/ ({fp_count} file(s))")

    if args.project:
        project = os.path.expanduser(args.project)
        table_dir = os.path.dirname(project) if os.path.isfile(project) else project
        scope = "project"
    else:
        table_dir = _find_global_table_dir()
        scope = "global"

    added_sym = register_symbol(table_dir, component_name, paths['dest_sym'])
    print(f"Registering in {scope} sym-lib-table... {'done' if added_sym else 'already present'}")

    added_fp = register_footprint(table_dir, component_name, paths['dest_fp_dir'])
    print(f"Registering in {scope} fp-lib-table...  {'done' if added_fp else 'already present'}")
