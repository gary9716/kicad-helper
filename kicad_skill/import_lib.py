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
