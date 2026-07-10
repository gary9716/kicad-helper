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
