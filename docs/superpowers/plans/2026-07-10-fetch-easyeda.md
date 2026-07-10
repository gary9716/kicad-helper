# Fetch EasyEDA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `fetch-easyeda` subcommand to `kicad-helper` that pulls a component (symbol + footprint + 3D model) from EasyEDA/LCSC via the `easyeda2kicad` CLI and imports it into the local library using the same registration flow as `import-lib`.

**Architecture:** `easyeda2kicad --full --lcsc_id=<id> --output <staging>/raw` produces flat `raw.kicad_sym` / `raw.pretty/` / `raw.3dshapes/`. `kicad_skill/fetch_easyeda.py` restructures that into a `KiCADv6/` tree shaped exactly like the Ultra Librarian source `import-lib` already understands, then reuses `import_lib.py`'s `validate_source`/`copy_component` plus a newly-extracted `register_and_check` helper (the registration+namespace-check tail of `handle_import_lib`, factored out so both commands share it instead of duplicating it).

**Tech Stack:** Python stdlib (`subprocess`, `shutil`, `tempfile`, `argparse`), `easyeda2kicad` (new pip dependency), `unittest` + `unittest.mock`.

Spec: `docs/superpowers/specs/2026-07-10-fetch-easyeda-design.md`

---

### Task 1: Extract `register_and_check` and `_resolve_table_scope` from `handle_import_lib`

Behavior-preserving refactor. `handle_import_lib`'s tail (register symbol → register footprint → namespace check) becomes a standalone function so `fetch-easyeda` can reuse it without duplicating ~20 lines. Table-dir/scope resolution (project vs. global) also gets extracted since both commands need it.

**Files:**
- Modify: `kicad_skill/import_lib.py:156-198` (`handle_import_lib`)
- Test: `tests/test_import_lib.py`

- [ ] **Step 1: Write failing tests for the two new functions**

Add to `tests/test_import_lib.py`, in a new class after `TestFindGlobalTableDir`:

```python
class TestResolveTableScope(unittest.TestCase):
    def test_project_file_path_uses_its_dirname(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, 'sub', 'my.kicad_pro')
            os.makedirs(os.path.dirname(proj))
            open(proj, 'w').close()
            from kicad_skill.import_lib import _resolve_table_scope
            table_dir, scope = _resolve_table_scope(proj)
            self.assertEqual(table_dir, os.path.dirname(proj))
            self.assertEqual(scope, 'project')

    def test_project_dir_path_used_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            from kicad_skill.import_lib import _resolve_table_scope
            table_dir, scope = _resolve_table_scope(d)
            self.assertEqual(table_dir, d)
            self.assertEqual(scope, 'project')

    def test_no_project_falls_back_to_global(self):
        from kicad_skill.import_lib import _resolve_table_scope, _find_global_table_dir
        table_dir, scope = _resolve_table_scope(None)
        self.assertEqual(table_dir, _find_global_table_dir())
        self.assertEqual(scope, 'global')


class TestRegisterAndCheck(unittest.TestCase):
    def _make_component(self, tmp, footprint_value):
        """dest_sym/dest_fp_dir shaped like copy_component()'s return value."""
        sym = os.path.join(tmp, 'PART.kicad_sym')
        content = (
            '(kicad_symbol_lib (version 20211014)\n'
            '  (symbol "PART"\n'
            f'    (property "Footprint" "{footprint_value}" (id 2) (at 0 0 0))\n'
            '  )\n)'
        )
        with open(sym, 'w') as f:
            f.write(content)
        fp_dir = os.path.join(tmp, 'footprints.pretty')
        os.makedirs(fp_dir)
        return {'dest_sym': sym, 'dest_fp_dir': fp_dir}

    def test_registers_symbol_and_footprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._make_component(tmp, 'PART:PKG')
            from kicad_skill.import_lib import register_and_check
            register_and_check(paths, 'PART', tmp, 'global', fix_namespace=False)
            with open(os.path.join(tmp, 'sym-lib-table')) as f:
                self.assertIn('(name "PART")', f.read())
            with open(os.path.join(tmp, 'fp-lib-table')) as f:
                self.assertIn('(name "PART")', f.read())

    def test_fix_namespace_patches_bare_footprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._make_component(tmp, 'PKG')
            from kicad_skill.import_lib import register_and_check, check_footprint_namespace
            register_and_check(paths, 'PART', tmp, 'global', fix_namespace=True)
            after = check_footprint_namespace(paths['dest_sym'], 'PART')
            self.assertEqual(after['missing'], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_import_lib -v`
Expected: `TestResolveTableScope` and `TestRegisterAndCheck` tests FAIL with `ImportError: cannot import name '_resolve_table_scope'` / `'register_and_check'`.

- [ ] **Step 3: Extract the two functions in `import_lib.py`**

Replace `handle_import_lib` (currently `kicad_skill/import_lib.py:156-198`) with:

```python
def _resolve_table_scope(project_arg) -> tuple:
    """Return (table_dir, scope) — 'project' if project_arg given, else 'global'."""
    if project_arg:
        project = os.path.expanduser(project_arg)
        table_dir = os.path.dirname(project) if os.path.isfile(project) else project
        return table_dir, "project"
    return _find_global_table_dir(), "global"


def register_and_check(paths: dict, component_name: str, table_dir: str, scope: str, fix_namespace: bool) -> None:
    """Register a copied component's symbol+footprint in the lib tables and report Footprint namespace issues."""
    added_sym = register_symbol(table_dir, component_name, paths['dest_sym'])
    print(f"Registering in {scope} sym-lib-table... {'done' if added_sym else 'already present'}")

    added_fp = register_footprint(table_dir, component_name, paths['dest_fp_dir'])
    print(f"Registering in {scope} fp-lib-table...  {'done' if added_fp else 'already present'}")

    ns_issues = check_footprint_namespace(paths['dest_sym'], component_name)
    if ns_issues['missing']:
        if fix_namespace:
            fixed = fix_footprint_namespace(paths['dest_sym'], component_name, ns_issues['missing'])
            print(f"Namespace check: fixed {fixed} bare Footprint reference(s) -> \"{component_name}:...\"")
        else:
            print(f"Namespace check: WARNING — {len(ns_issues['missing'])} Footprint reference(s) have no library "
                  f"prefix (e.g. \"{ns_issues['missing'][0]}\" instead of \"{component_name}:{ns_issues['missing'][0]}\"). "
                  f"KiCad will not resolve these until fixed. Re-run with --fix-namespace to patch automatically.")
    if ns_issues['mismatched']:
        print(f"Namespace check: WARNING — {len(ns_issues['mismatched'])} Footprint reference(s) point to a "
              f"different library than the one just registered ({component_name}): {ns_issues['mismatched']}. "
              f"Not auto-fixed — verify this is intentional (shared footprint library).")
    if not ns_issues['missing'] and not ns_issues['mismatched']:
        print("Namespace check: OK — all Footprint references resolve to the registered library.")


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

    table_dir, scope = _resolve_table_scope(args.project)
    register_and_check(paths, component_name, table_dir, scope, args.fix_namespace)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_import_lib -v`
Expected: all tests PASS (existing `import-lib` tests unmodified and still green — behavior-preserving refactor).

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/import_lib.py tests/test_import_lib.py
git commit -m "refactor: extract register_and_check + _resolve_table_scope from handle_import_lib"
```

---

### Task 2: `fetch_easyeda_component` — subprocess call to `easyeda2kicad`

**Files:**
- Create: `kicad_skill/fetch_easyeda.py`
- Test: `tests/test_fetch_easyeda.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_easyeda.py`:

```python
import unittest
import os
import sys
import subprocess
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFetchEasyedaComponent(unittest.TestCase):
    @patch('kicad_skill.fetch_easyeda.subprocess.run')
    def test_calls_cli_with_full_and_lcsc_id(self, mock_run):
        from kicad_skill.fetch_easyeda import fetch_easyeda_component
        with tempfile.TemporaryDirectory() as staging:
            base = fetch_easyeda_component('C2040', staging)
            self.assertEqual(base, os.path.join(staging, 'raw'))
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd[0], 'easyeda2kicad')
            self.assertIn('--full', cmd)
            self.assertIn('--lcsc_id=C2040', cmd)
            self.assertIn('--output', cmd)
            self.assertIn(os.path.join(staging, 'raw'), cmd)
            self.assertTrue(mock_run.call_args[1].get('check'))

    @patch('kicad_skill.fetch_easyeda.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'easyeda2kicad'))
    def test_propagates_cli_failure(self, mock_run):
        from kicad_skill.fetch_easyeda import fetch_easyeda_component
        with tempfile.TemporaryDirectory() as staging:
            with self.assertRaises(subprocess.CalledProcessError):
                fetch_easyeda_component('BADID', staging)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kicad_skill.fetch_easyeda'`

- [ ] **Step 3: Write minimal implementation**

Create `kicad_skill/fetch_easyeda.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/fetch_easyeda.py tests/test_fetch_easyeda.py
git commit -m "feat: add fetch_easyeda_component subprocess wrapper"
```

---

### Task 3: `restructure_to_kicadv6` — reshape flat output into `KiCADv6/` tree

**Files:**
- Modify: `kicad_skill/fetch_easyeda.py`
- Test: `tests/test_fetch_easyeda.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_easyeda.py`:

```python
class TestRestructureToKicadv6(unittest.TestCase):
    def _make_raw_output(self, staging, with_3d=True):
        raw_base = os.path.join(staging, 'raw')
        open(raw_base + '.kicad_sym', 'w').close()
        os.makedirs(raw_base + '.pretty')
        open(os.path.join(raw_base + '.pretty', 'PKG.kicad_mod'), 'w').close()
        if with_3d:
            os.makedirs(raw_base + '.3dshapes')
            open(os.path.join(raw_base + '.3dshapes', 'model.step'), 'w').close()
        return raw_base

    def test_moves_sym_and_footprints_into_kicadv6(self):
        with tempfile.TemporaryDirectory() as staging:
            raw_base = self._make_raw_output(staging)
            from kicad_skill.fetch_easyeda import restructure_to_kicadv6
            result = restructure_to_kicadv6(staging, raw_base, 'C2040')
            self.assertEqual(result, staging)
            kv6 = os.path.join(staging, 'KiCADv6')
            self.assertTrue(os.path.exists(os.path.join(kv6, 'C2040.kicad_sym')))
            self.assertTrue(os.path.isdir(os.path.join(kv6, 'footprints.pretty')))
            self.assertTrue(os.path.exists(os.path.join(kv6, 'footprints.pretty', 'PKG.kicad_mod')))

    def test_moves_3dshapes_when_present(self):
        with tempfile.TemporaryDirectory() as staging:
            raw_base = self._make_raw_output(staging, with_3d=True)
            from kicad_skill.fetch_easyeda import restructure_to_kicadv6
            restructure_to_kicadv6(staging, raw_base, 'C2040')
            kv6 = os.path.join(staging, 'KiCADv6')
            self.assertTrue(os.path.isdir(os.path.join(kv6, '3dshapes')))
            self.assertTrue(os.path.exists(os.path.join(kv6, '3dshapes', 'model.step')))

    def test_no_3dshapes_dir_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as staging:
            raw_base = self._make_raw_output(staging, with_3d=False)
            from kicad_skill.fetch_easyeda import restructure_to_kicadv6
            restructure_to_kicadv6(staging, raw_base, 'C2040')
            kv6 = os.path.join(staging, 'KiCADv6')
            self.assertFalse(os.path.exists(os.path.join(kv6, '3dshapes')))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: FAIL with `ImportError: cannot import name 'restructure_to_kicadv6'`

- [ ] **Step 3: Write minimal implementation**

Append to `kicad_skill/fetch_easyeda.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/fetch_easyeda.py tests/test_fetch_easyeda.py
git commit -m "feat: add restructure_to_kicadv6"
```

---

### Task 4: `import_fetched_component` — wire staging tree into the existing import flow

**Files:**
- Modify: `kicad_skill/fetch_easyeda.py`
- Test: `tests/test_fetch_easyeda.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_easyeda.py`:

```python
class TestImportFetchedComponent(unittest.TestCase):
    def _make_staged_kicadv6(self, staging, footprint_value='C2040:PKG'):
        kv6 = os.path.join(staging, 'KiCADv6')
        os.makedirs(kv6)
        sym = os.path.join(kv6, 'C2040.kicad_sym')
        content = (
            '(kicad_symbol_lib (version 20211014)\n'
            '  (symbol "C2040"\n'
            f'    (property "Footprint" "{footprint_value}" (id 2) (at 0 0 0))\n'
            '  )\n)'
        )
        with open(sym, 'w') as f:
            f.write(content)
        fp_dir = os.path.join(kv6, 'footprints.pretty')
        os.makedirs(fp_dir)
        open(os.path.join(fp_dir, 'PKG.kicad_mod'), 'w').close()

    def test_copies_and_registers(self):
        with tempfile.TemporaryDirectory() as staging, tempfile.TemporaryDirectory() as lib_root, tempfile.TemporaryDirectory() as table_dir:
            self._make_staged_kicadv6(staging)
            from kicad_skill.fetch_easyeda import import_fetched_component
            paths = import_fetched_component(staging, 'C2040', lib_root, table_dir, 'global')
            self.assertTrue(os.path.exists(paths['dest_sym']))
            self.assertTrue(os.path.isdir(paths['dest_fp_dir']))
            with open(os.path.join(table_dir, 'sym-lib-table')) as f:
                self.assertIn('(name "C2040")', f.read())
            with open(os.path.join(table_dir, 'fp-lib-table')) as f:
                self.assertIn('(name "C2040")', f.read())

    def test_raises_on_existing_dest_without_force(self):
        with tempfile.TemporaryDirectory() as staging, tempfile.TemporaryDirectory() as lib_root, tempfile.TemporaryDirectory() as table_dir:
            self._make_staged_kicadv6(staging)
            from kicad_skill.fetch_easyeda import import_fetched_component
            import_fetched_component(staging, 'C2040', lib_root, table_dir, 'global')
            self._make_staged_kicadv6(staging)  # re-populate KiCADv6 (import moved nothing, just copied)
            with self.assertRaises(FileExistsError):
                import_fetched_component(staging, 'C2040', lib_root, table_dir, 'global', force=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: FAIL with `ImportError: cannot import name 'import_fetched_component'`

- [ ] **Step 3: Write minimal implementation**

Append to `kicad_skill/fetch_easyeda.py`, and add the import at the top:

```python
from .import_lib import validate_source, copy_component, register_and_check
```

```python
def import_fetched_component(staging_root: str, component_name: str, lib_root: str,
                              table_dir: str, scope: str, force: bool = False,
                              fix_namespace: bool = False) -> dict:
    """Validate a staged KiCADv6/ tree, copy it into lib_root, and register it.

    Mirrors import_lib.handle_import_lib's body, minus argparse — reused by
    the fetch-easyeda CLI handler.
    """
    validate_source(staging_root)
    paths = copy_component(staging_root, lib_root, component_name, force=force)
    register_and_check(paths, component_name, table_dir, scope, fix_namespace)
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_fetch_easyeda -v`
Expected: PASS (7 tests total)

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/fetch_easyeda.py tests/test_fetch_easyeda.py
git commit -m "feat: add import_fetched_component, wiring staged output into import_lib flow"
```

---

### Task 5: `pyproject.toml` — add `easyeda2kicad` dependency

**Files:**
- Modify: `pyproject.toml:7`

- [ ] **Step 1: Add the dependency**

```bash
cd /Users/gary/kicad-helper && uv add easyeda2kicad
```

Expected: `pyproject.toml` line 7 becomes `dependencies = ["easyeda2kicad>=X.Y.Z"]` (exact pinned version from PyPI resolution), `uv.lock` updated, and `uv run easyeda2kicad --help` prints its usage text (console script now on the venv PATH).

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add easyeda2kicad dependency"
```

---

### Task 6: `fetch-easyeda` subcommand in `main.py`

**Files:**
- Modify: `kicad_skill/main.py:413-420` (near the `import-lib` parser) and `kicad_skill/main.py:462-464` (dispatch)

- [ ] **Step 1: Add the handler function**

Insert after `handle_snapshot` (before `def main():`, i.e. after `kicad_skill/main.py:314`):

```python
def handle_fetch_easyeda(args):
    import tempfile
    import glob
    import subprocess
    from .fetch_easyeda import fetch_easyeda_component, restructure_to_kicadv6, import_fetched_component
    from .import_lib import _resolve_table_scope

    component_name = args.name or args.lcsc_id
    lib_root = os.path.expanduser(args.lib_root)

    with tempfile.TemporaryDirectory() as staging:
        print(f"Fetching {args.lcsc_id} from EasyEDA...")
        try:
            raw_base = fetch_easyeda_component(args.lcsc_id, staging)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # easyeda2kicad already printed its own error to stderr (inherited, not captured) —
            # no extra wrapping, just a clean non-zero exit instead of a Python traceback.
            sys.exit(1)

        restructure_to_kicadv6(staging, raw_base, component_name)

        table_dir, scope = _resolve_table_scope(args.project)

        print(f"Importing {component_name} → {os.path.join(lib_root, component_name)}/KiCADv6/")
        paths = import_fetched_component(
            staging, component_name, lib_root, table_dir, scope,
            force=args.force, fix_namespace=args.fix_namespace,
        )

        fp_count = len(glob.glob(os.path.join(paths['dest_fp_dir'], '*.kicad_mod')))
        print(f"  symbol:    {os.path.basename(paths['dest_sym'])}")
        print(f"  footprint: footprints.pretty/ ({fp_count} file(s))")
```

- [ ] **Step 2: Add the subparser**

In `main()`, immediately after the `import-lib` parser block (`kicad_skill/main.py:413-419`, right before `args = parser.parse_args()`), insert:

```python
    # fetch-easyeda parser
    fetch_easyeda_parser = subparsers.add_parser("fetch-easyeda", help="Fetch a component from EasyEDA/LCSC via easyeda2kicad and import it into local library")
    fetch_easyeda_parser.add_argument("lcsc_id", help="LCSC part number, e.g. C2040")
    fetch_easyeda_parser.add_argument("--name", default=None, help="Component/library name override (default: lcsc_id)")
    fetch_easyeda_parser.add_argument("--lib-root", default="~/hardwares/Libraries", help="Root directory for installed libraries (default: ~/hardwares/Libraries)")
    fetch_easyeda_parser.add_argument("--project", default=None, help="Path to .kicad_pro for project-level registration (default: global)")
    fetch_easyeda_parser.add_argument("--force", action="store_true", help="Overwrite if component already exists in lib-root")
    fetch_easyeda_parser.add_argument("--fix-namespace", action="store_true", help="Auto-prepend the registered library name to bare (unnamespaced) Footprint properties")
```

- [ ] **Step 3: Add the dispatch branch**

In `main()`'s dispatch chain, right after the `elif args.command == 'import-lib':` block (`kicad_skill/main.py:462-464`), insert:

```python
    elif args.command == 'fetch-easyeda':
        handle_fetch_easyeda(args)
```

- [ ] **Step 4: Verify CLI wiring manually**

Run: `cd /Users/gary/kicad-helper && uv run python -m kicad_skill.main fetch-easyeda --help`
Expected: argparse help text listing `lcsc_id`, `--name`, `--lib-root`, `--project`, `--force`, `--fix-namespace`.

Run: `cd /Users/gary/kicad-helper && uv run python -m kicad_skill.main fetch-easyeda C2040 --lib-root /tmp/kicad-helper-test-libs`
Expected: either a successful fetch+import (prints `Fetching...` / `Importing...` / symbol+footprint summary, assuming network access and a valid LCSC id), or a clean non-zero-exit failure with `easyeda2kicad`'s own error surfaced — not a Python traceback from our code.

- [ ] **Step 5: Commit**

```bash
git add kicad_skill/main.py
git commit -m "feat: add fetch-easyeda subcommand"
```

---

### Task 7: Full test suite + skill doc update

**Files:**
- Modify: `skills/kicad-helper/SKILL.md` (subcommand reference)

- [ ] **Step 1: Run the full suite**

Run: `cd /Users/gary/kicad-helper && uv run python -m unittest discover -s tests -v`
Expected: all tests PASS, including `tests/test_import_lib.py` and `tests/test_fetch_easyeda.py`.

- [ ] **Step 2: Add `fetch-easyeda` to the skill reference**

Read `skills/kicad-helper/SKILL.md`, find the `import-lib` entry, add a `fetch-easyeda` entry directly after it in the same format (subcommand name, one-line description, args table) — match whatever format the existing `import-lib` entry uses in that file.

- [ ] **Step 3: Commit**

```bash
git add skills/kicad-helper/SKILL.md
git commit -m "docs: document fetch-easyeda subcommand"
```
