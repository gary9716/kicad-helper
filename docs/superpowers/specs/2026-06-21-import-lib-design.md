# KiCad Library Import Design

## Overview

New `import-lib` subcommand for `kicad-helper`. Imports Ultra Librarian KiCad v6+ component packages (symbol + footprint) into a local library root and registers them in KiCad's sym-lib-table / fp-lib-table.

## Command Interface

```
kicad-helper import-lib <source_path> [--lib-root PATH] [--project PATH] [--force]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `source_path` | required | Path to UL download folder (e.g. `~/Downloads/ul_TPS54540DDAR`) |
| `--lib-root` | `~/hardwares/Libraries` | Root directory for installed libraries |
| `--project` | none (global) | Path to `.kicad_pro`; switches to project-level table registration |
| `--force` | false | Overwrite if component already exists in lib-root |

## Source Format (Ultra Librarian KiCad v6+)

```
ul_TPS54540DDAR/
  KiCADv6/
    2026-06-21_09-15-50.kicad_sym   ← exactly one .kicad_sym
    footprints.pretty/
      *.kicad_mod                   ← one or more footprints
  readme.txt
  ImportGuides.html
```

## Architecture

**New file:** `kicad_skill/import_lib.py`  
**Modified:** `kicad_skill/main.py` — add `import-lib` subparser + handler

### import_lib.py responsibilities

1. **validate_source(source_path)** — check `KiCADv6/*.kicad_sym` exists, `footprints.pretty/` exists
2. **copy_component(source_path, lib_root, component_name, force)** — copy `KiCADv6/` tree to `<lib_root>/<component_name>/KiCADv6/`
3. **find_global_table_dir()** — scan `~/Library/Preferences/kicad/` for latest version dir
4. **resolve_table_dir(project_path)** — return dir containing project's sym/fp-lib-table
5. **register_symbol(table_path, name, sym_uri)** — inject entry into sym-lib-table if not present
6. **register_footprint(table_path, name, fp_uri)** — inject entry into fp-lib-table if not present

### S-expression injection (no full parser)

Tables are flat — each entry is a single `(lib ...)` line. Strategy:

```python
def _inject_lib_entry(content: str, new_entry: str) -> str:
    if f'(name "{name}")' in content:
        return None  # already registered
    return content.rstrip().rstrip(')') + '\n' + new_entry + '\n)'
```

### URI format

- Symbol: absolute path to `.kicad_sym` file
- Footprint: absolute path to `.pretty` directory

Absolute paths used (not `${KIPRJMOD}`) for global installs. Project-level installs use relative paths from project dir.

## Data Flow

```
source_path/KiCADv6/
    → copy → <lib_root>/<component_name>/KiCADv6/
    → register sym → sym-lib-table  (name=component_name, uri=<abs_path>.kicad_sym)
    → register fp  → fp-lib-table   (name=component_name, uri=<abs_path>.pretty)
```

## Error Handling

| Condition | Behavior |
|-----------|----------|
| No `KiCADv6/` in source | Fatal error, clear message |
| No `.kicad_sym` found | Fatal error |
| Destination already exists | Error unless `--force` |
| Already registered in table | Skip, print warning |
| Table file missing | Create minimal table from scratch |

## Output

```
Copying ul_TPS54540DDAR → /Users/gary/hardwares/Libraries/ul_TPS54540DDAR/KiCADv6/
  symbol:    2026-06-21_09-15-50.kicad_sym
  footprint: footprints.pretty/ (3 files)
Registering in global sym-lib-table... done
Registering in global fp-lib-table... done
```

## Files Changed

- `kicad_skill/import_lib.py` (new)
- `kicad_skill/main.py` (add subcommand)
