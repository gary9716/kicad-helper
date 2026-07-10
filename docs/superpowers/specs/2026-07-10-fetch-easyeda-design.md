# EasyEDA/LCSC Component Fetch Design

## Overview

New `fetch-easyeda` subcommand for `kicad-helper`. Fetches a component (symbol + footprint + 3D model) from EasyEDA/LCSC via `easyeda2kicad.py`, restructures its output into the same `KiCADv6/` layout used by `import-lib`, then reuses the existing import/registration flow (copy into lib-root, register in sym-lib-table/fp-lib-table, namespace check).

## Command Interface

```
kicad-helper fetch-easyeda <lcsc_id> [--name NAME] [--lib-root PATH] [--project PATH] [--force] [--fix-namespace]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `lcsc_id` | required | LCSC part number, e.g. `C2040` |
| `--name` | `lcsc_id` | Library/component name override (dest folder name, table entry name) |
| `--lib-root` | `~/hardwares/Libraries` | Root directory for installed libraries |
| `--project` | none (global) | Path to `.kicad_pro`; switches to project-level table registration |
| `--force` | false | Overwrite if component already exists in lib-root |
| `--fix-namespace` | false | Auto-prepend registered lib name to bare Footprint refs |

## Dependency

`easyeda2kicad` added as a formal `pyproject.toml` dependency (`uv add easyeda2kicad`). Its console script `easyeda2kicad` is invoked via `subprocess`, not its Python API.

## Upstream CLI (easyeda2kicad.py, verified against README)

```
easyeda2kicad --full --lcsc_id=<id> --output <base_path>
```

Produces (flat, not `KiCADv6/`-shaped):
```
<base_path>.kicad_sym
<base_path>.pretty/       *.kicad_mod
<base_path>.3dshapes/     *.step, *.wrl
```

## Architecture

**New file:** `kicad_skill/fetch_easyeda.py`
**Modified:** `kicad_skill/import_lib.py` — extract shared post-copy step
**Modified:** `kicad_skill/main.py` — add `fetch-easyeda` subparser + handler

### fetch_easyeda.py responsibilities

1. **fetch_easyeda_component(lcsc_id, staging_dir) -> dict** — run `easyeda2kicad --full --lcsc_id=<id> --output <staging_dir>/raw` via subprocess. Non-zero exit: print upstream stderr as-is, raise (handler exits non-zero). No message wrapping.
2. **restructure_to_kicadv6(staging_dir) -> str** — move `raw.kicad_sym` → `KiCADv6/<name>.kicad_sym`, `raw.pretty/` → `KiCADv6/footprints.pretty/`, `raw.3dshapes/` → `KiCADv6/3dshapes/`. Returns path to the `staging_dir/KiCADv6` root (shape matches what `import_lib.validate_source()` expects).

### import_lib.py refactor

Extract the tail of `handle_import_lib` (register_symbol → register_footprint → namespace check block) into:

```python
def register_and_check(paths, component_name, table_dir, scope, fix_namespace) -> None
```

`handle_import_lib` and the new `handle_fetch_easyeda` both call `validate_source` → `copy_component` → `register_and_check`, avoiding duplicated registration logic.

### main.py: handle_fetch_easyeda

```
1. component_name = args.name or args.lcsc_id
2. tempfile.TemporaryDirectory() as staging
3. fetch_easyeda_component(args.lcsc_id, staging)
4. kicadv6_root = restructure_to_kicadv6(staging)
5. validate_source(kicadv6_root's parent)   # reuse as-is
6. copy_component(...) -> paths             # reuse as-is
7. resolve table_dir/scope (same logic as handle_import_lib)
8. register_and_check(paths, component_name, table_dir, scope, args.fix_namespace)
9. staging auto-cleaned on context exit
```

## Data Flow

```
lcsc_id
  → subprocess easyeda2kicad --full --output <tmp>/raw
  → restructure → <tmp>/KiCADv6/{name.kicad_sym, footprints.pretty/, 3dshapes/}
  → copy_component → <lib_root>/<name>/KiCADv6/
  → register_and_check → sym-lib-table + fp-lib-table + namespace check
  → tmp dir removed
```

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `easyeda2kicad` not installed / not on PATH | subprocess raises `FileNotFoundError`; let it propagate, non-zero exit |
| LCSC id not found / API failure | upstream CLI prints its own error to stderr, exits non-zero; handler mirrors exit code, no extra wrapping |
| Destination already exists | same as `import-lib`: error unless `--force` |
| Already registered in table | same as `import-lib`: skip, print warning |

3D model paths embedded in generated footprints may be relative/env-var-based from `easyeda2kicad`'s own output layout; no path-rewriting is attempted after the move — out of scope for this change, user fixes 3D model path in KiCad manually if broken.

## Testing

`tests/test_fetch_easyeda.py` — mocks `subprocess.run` to write fake `raw.kicad_sym` / `raw.pretty/*.kicad_mod` / `raw.3dshapes/*.step` into a temp dir, verifies `restructure_to_kicadv6` produces correct `KiCADv6/` layout, and that the full `handle_fetch_easyeda` flow registers correctly in sym-lib-table/fp-lib-table (reusing `import_lib` test patterns). No real network/API calls.

## Files Changed

- `kicad_skill/fetch_easyeda.py` (new)
- `kicad_skill/import_lib.py` (extract `register_and_check`, used by both handlers)
- `kicad_skill/main.py` (add `fetch-easyeda` subcommand)
- `pyproject.toml` (add `easyeda2kicad` dependency)
- `tests/test_fetch_easyeda.py` (new)
