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
