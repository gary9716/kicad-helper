import os
import shutil
import glob


def _inject_lib_entry(content: str, name: str, new_entry: str):
    """Inject (lib ...) entry before closing paren. Returns None if name already present."""
    if f'(name "{name}")' in content:
        return None
    return content.rstrip().rstrip(')') + '\n' + new_entry + '\n)'
