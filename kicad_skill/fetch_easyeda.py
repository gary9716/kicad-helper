import os
import shutil
import subprocess


def fetch_easyeda_component(lcsc_id: str, staging_dir: str) -> str:
    """Run `easyeda2kicad --full` for lcsc_id, writing output under staging_dir/raw.*

    Returns the raw output base path (no extension) — <staging_dir>/raw.
    Non-zero exit / missing binary propagates as-is (CalledProcessError / FileNotFoundError);
    stderr is inherited straight to the terminal, no wrapping.
    """
    base = os.path.join(staging_dir, 'raw')
    cmd = ['easyeda2kicad', '--full', f'--lcsc_id={lcsc_id}', '--output', base]
    subprocess.run(cmd, check=True)
    return base


def restructure_to_kicadv6(staging_dir: str, raw_base: str, component_name: str) -> str:
    """Move easyeda2kicad's flat raw.* output into staging_dir/KiCADv6/, matching the
    layout import_lib.validate_source()/copy_component() expect. Returns staging_dir.
    """
    kv6 = os.path.join(staging_dir, 'KiCADv6')
    os.makedirs(kv6)

    shutil.move(raw_base + '.kicad_sym', os.path.join(kv6, f'{component_name}.kicad_sym'))
    shutil.move(raw_base + '.pretty', os.path.join(kv6, 'footprints.pretty'))

    shapes_src = raw_base + '.3dshapes'
    if os.path.isdir(shapes_src):
        shutil.move(shapes_src, os.path.join(kv6, '3dshapes'))

    return staging_dir
