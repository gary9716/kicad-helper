# Import-Lib Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `kicad-helper import-lib` subcommand that copies Ultra Librarian KiCad v6+ component folders into a local library root and registers them in KiCad's sym-lib-table and fp-lib-table.

**Architecture:** New `kicad_skill/import_lib.py` holds all logic (validate, copy, register). `main.py` gets a thin subcommand wiring. S-expression tables are manipulated with string injection — no parser needed since entries are flat single-line records.

**Tech Stack:** Python stdlib only (`os`, `shutil`, `glob`, `pathlib`), `unittest` for tests.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `kicad_skill/import_lib.py` | Create | All import logic |
| `kicad_skill/main.py` | Modify | Add subparser + dispatch |
| `tests/test_import_lib.py` | Create | All unit tests |

---

### Task 1: S-expression injection utility

**Files:**
- Create: `kicad_skill/import_lib.py`
- Create: `tests/test_import_lib.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_lib.py`:

```python
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.import_lib import _inject_lib_entry


class TestInjectLibEntry(unittest.TestCase):
    def test_injects_before_closing_paren(self):
        content = '(sym_lib_table\n  (version 7)\n)'
        result = _inject_lib_entry(content, 'MyLib', '  (lib (name "MyLib") (uri "/path/file.kicad_sym"))')
        self.assertIn('(name "MyLib")', result)
        self.assertTrue(result.endswith('\n)'))

    def test_returns_none_if_already_registered(self):
        content = '(sym_lib_table\n  (lib (name "MyLib") (uri "/path/file.kicad_sym"))\n)'
        result = _inject_lib_entry(content, 'MyLib', '  (lib (name "MyLib") (uri "/other"))')
        self.assertIsNone(result)

    def test_preserves_existing_entries(self):
        content = '(sym_lib_table\n  (version 7)\n  (lib (name "Existing") (uri "/a"))\n)'
        result = _inject_lib_entry(content, 'New', '  (lib (name "New") (uri "/b"))')
        self.assertIn('(name "Existing")', result)
        self.assertIn('(name "New")', result)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: `ImportError: cannot import name '_inject_lib_entry'`

- [ ] **Step 3: Create `kicad_skill/import_lib.py` with injection logic**

```python
import os
import shutil
import glob


def _inject_lib_entry(content: str, name: str, new_entry: str):
    """Inject (lib ...) entry before closing paren. Returns None if name already present."""
    if f'(name "{name}")' in content:
        return None
    return content.rstrip().rstrip(')') + '\n' + new_entry + '\n)'
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/import_lib.py tests/test_import_lib.py && git commit -m "feat(import-lib): S-expression injection utility"
```

---

### Task 2: Source validation

**Files:**
- Modify: `kicad_skill/import_lib.py`
- Modify: `tests/test_import_lib.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_lib.py` (inside the file, before `if __name__ == '__main__':`):

```python
import tempfile


class TestValidateSource(unittest.TestCase):
    def _make_ul_dir(self, tmp, has_sym=True, has_fp=True):
        """Build a minimal Ultra Librarian KiCADv6 folder structure."""
        kv6 = os.path.join(tmp, 'KiCADv6')
        os.makedirs(kv6)
        if has_sym:
            open(os.path.join(kv6, '2026-01-01_00-00-00.kicad_sym'), 'w').close()
        if has_fp:
            os.makedirs(os.path.join(kv6, 'footprints.pretty'))
        return tmp

    def test_valid_source_returns_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_ul_dir(tmp)
            from kicad_skill.import_lib import validate_source
            result = validate_source(tmp)
            self.assertIn('sym_path', result)
            self.assertIn('fp_dir', result)
            self.assertTrue(result['sym_path'].endswith('.kicad_sym'))
            self.assertTrue(os.path.isdir(result['fp_dir']))

    def test_missing_kicadv6_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            from kicad_skill.import_lib import validate_source
            with self.assertRaises(ValueError) as ctx:
                validate_source(tmp)
            self.assertIn('KiCADv6', str(ctx.exception))

    def test_missing_sym_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_ul_dir(tmp, has_sym=False)
            from kicad_skill.import_lib import validate_source
            with self.assertRaises(ValueError) as ctx:
                validate_source(tmp)
            self.assertIn('.kicad_sym', str(ctx.exception))

    def test_missing_fp_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_ul_dir(tmp, has_fp=False)
            from kicad_skill.import_lib import validate_source
            with self.assertRaises(ValueError) as ctx:
                validate_source(tmp)
            self.assertIn('footprints.pretty', str(ctx.exception))
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py::TestValidateSource -v
```
Expected: `ImportError: cannot import name 'validate_source'`

- [ ] **Step 3: Add `validate_source` to `kicad_skill/import_lib.py`**

Append to the file:

```python

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
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/import_lib.py tests/test_import_lib.py && git commit -m "feat(import-lib): source validation"
```

---

### Task 3: Component copy

**Files:**
- Modify: `kicad_skill/import_lib.py`
- Modify: `tests/test_import_lib.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_lib.py`:

```python
class TestCopyComponent(unittest.TestCase):
    def _make_ul_dir(self, tmp):
        kv6 = os.path.join(tmp, 'KiCADv6')
        os.makedirs(kv6)
        open(os.path.join(kv6, '2026-01-01_00-00-00.kicad_sym'), 'w').close()
        fp = os.path.join(kv6, 'footprints.pretty')
        os.makedirs(fp)
        open(os.path.join(fp, 'PKG.kicad_mod'), 'w').close()
        return tmp

    def test_copies_to_lib_root(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as lib_root:
            self._make_ul_dir(src)
            from kicad_skill.import_lib import copy_component
            result = copy_component(src, lib_root, 'ul_TEST')
            self.assertTrue(os.path.exists(result['dest_sym']))
            self.assertTrue(os.path.isdir(result['dest_fp_dir']))
            self.assertTrue(result['dest_sym'].endswith('.kicad_sym'))

    def test_raises_if_dest_exists_without_force(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as lib_root:
            self._make_ul_dir(src)
            from kicad_skill.import_lib import copy_component
            copy_component(src, lib_root, 'ul_TEST')
            with self.assertRaises(FileExistsError):
                copy_component(src, lib_root, 'ul_TEST', force=False)

    def test_force_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as lib_root:
            self._make_ul_dir(src)
            from kicad_skill.import_lib import copy_component
            copy_component(src, lib_root, 'ul_TEST')
            result = copy_component(src, lib_root, 'ul_TEST', force=True)
            self.assertTrue(os.path.exists(result['dest_sym']))
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py::TestCopyComponent -v
```
Expected: `ImportError: cannot import name 'copy_component'`

- [ ] **Step 3: Add `copy_component` to `kicad_skill/import_lib.py`**

Append to the file:

```python

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
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/import_lib.py tests/test_import_lib.py && git commit -m "feat(import-lib): component copy with force flag"
```

---

### Task 4: Table registration

**Files:**
- Modify: `kicad_skill/import_lib.py`
- Modify: `tests/test_import_lib.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_lib.py`:

```python
class TestTableRegistration(unittest.TestCase):
    def test_register_symbol_adds_entry(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'sym-lib-table')
            with open(table, 'w') as f:
                f.write('(sym_lib_table\n  (version 7)\n)')
            from kicad_skill.import_lib import register_symbol
            result = register_symbol(d, 'ul_TEST', '/path/test.kicad_sym')
            self.assertTrue(result)
            content = open(table).read()
            self.assertIn('(name "ul_TEST")', content)
            self.assertIn('/path/test.kicad_sym', content)

    def test_register_symbol_skips_if_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'sym-lib-table')
            with open(table, 'w') as f:
                f.write('(sym_lib_table\n  (lib (name "ul_TEST") (uri "/path/test.kicad_sym"))\n)')
            from kicad_skill.import_lib import register_symbol
            result = register_symbol(d, 'ul_TEST', '/path/test.kicad_sym')
            self.assertFalse(result)

    def test_register_symbol_creates_table_if_missing(self):
        with tempfile.TemporaryDirectory() as d:
            from kicad_skill.import_lib import register_symbol
            result = register_symbol(d, 'ul_TEST', '/path/test.kicad_sym')
            self.assertTrue(result)
            self.assertTrue(os.path.exists(os.path.join(d, 'sym-lib-table')))

    def test_register_footprint_adds_entry(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'fp-lib-table')
            with open(table, 'w') as f:
                f.write('(fp_lib_table\n  (version 7)\n)')
            from kicad_skill.import_lib import register_footprint
            result = register_footprint(d, 'ul_TEST', '/path/test.pretty')
            self.assertTrue(result)
            content = open(table).read()
            self.assertIn('(name "ul_TEST")', content)

    def test_register_footprint_skips_if_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'fp-lib-table')
            with open(table, 'w') as f:
                f.write('(fp_lib_table\n  (lib (name "ul_TEST") (uri "/path/test.pretty"))\n)')
            from kicad_skill.import_lib import register_footprint
            result = register_footprint(d, 'ul_TEST', '/path/test.pretty')
            self.assertFalse(result)
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py::TestTableRegistration -v
```
Expected: `ImportError: cannot import name 'register_symbol'`

- [ ] **Step 3: Add registration functions to `kicad_skill/import_lib.py`**

Append to the file:

```python

_MINIMAL_SYM_TABLE = '(sym_lib_table\n  (version 7)\n)'
_MINIMAL_FP_TABLE = '(fp_lib_table\n  (version 7)\n)'


def register_symbol(table_dir: str, name: str, sym_uri: str) -> bool:
    """Add symbol lib entry to sym-lib-table. Returns True if added, False if already present."""
    table_path = os.path.join(table_dir, 'sym-lib-table')
    content = open(table_path).read() if os.path.exists(table_path) else _MINIMAL_SYM_TABLE
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
    content = open(table_path).read() if os.path.exists(table_path) else _MINIMAL_FP_TABLE
    entry = f'  (lib (name "{name}") (type "KiCad") (uri "{fp_uri}") (options "") (descr ""))'
    result = _inject_lib_entry(content, name, entry)
    if result is None:
        return False
    with open(table_path, 'w') as f:
        f.write(result)
    return True
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/import_lib.py tests/test_import_lib.py && git commit -m "feat(import-lib): sym/fp table registration"
```

---

### Task 5: Handler and global table directory detection

**Files:**
- Modify: `kicad_skill/import_lib.py`
- Modify: `tests/test_import_lib.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_import_lib.py`:

```python
class TestFindGlobalTableDir(unittest.TestCase):
    def test_returns_latest_version_dir(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, '9.0'))
            os.makedirs(os.path.join(base, '10.0'))
            from kicad_skill.import_lib import _find_global_table_dir
            result = _find_global_table_dir(base)
            self.assertTrue(result.endswith('10.0'))

    def test_raises_if_base_missing(self):
        from kicad_skill.import_lib import _find_global_table_dir
        with self.assertRaises(FileNotFoundError):
            _find_global_table_dir('/nonexistent/path/kicad')
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py::TestFindGlobalTableDir -v
```
Expected: `ImportError: cannot import name '_find_global_table_dir'`

- [ ] **Step 3: Add `_find_global_table_dir` and `handle_import_lib` to `kicad_skill/import_lib.py`**

Append to the file:

```python

_DEFAULT_KICAD_PREFS = os.path.expanduser('~/Library/Preferences/kicad')


def _find_global_table_dir(base: str = _DEFAULT_KICAD_PREFS) -> str:
    """Return path to latest KiCad version config dir."""
    if not os.path.isdir(base):
        raise FileNotFoundError(f"KiCad config not found at {base}")
    versions = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    if not versions:
        raise FileNotFoundError(f"No KiCad version dirs found in {base}")
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
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/test_import_lib.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/import_lib.py tests/test_import_lib.py && git commit -m "feat(import-lib): handler and global config detection"
```

---

### Task 6: Wire subcommand into `main.py`

**Files:**
- Modify: `kicad_skill/main.py`

- [ ] **Step 1: Add import-lib subparser**

In `kicad_skill/main.py`, after the `resolve_parser` block (around line 410), add:

```python
    # import-lib parser
    import_lib_parser = subparsers.add_parser("import-lib", help="Import a KiCad v6+ Ultra Librarian component into local library")
    import_lib_parser.add_argument("source_path", help="Path to the Ultra Librarian download folder")
    import_lib_parser.add_argument("--lib-root", default="~/hardwares/Libraries", help="Root directory for installed libraries (default: ~/hardwares/Libraries)")
    import_lib_parser.add_argument("--project", default=None, help="Path to .kicad_pro for project-level registration (default: global)")
    import_lib_parser.add_argument("--force", action="store_true", help="Overwrite if component already exists in lib-root")
```

- [ ] **Step 2: Add dispatch branch**

In `main.py`, inside the `if args.command == ...` chain, add before the `else: parser.print_help()` line:

```python
    elif args.command == 'import-lib':
        from .import_lib import handle_import_lib
        handle_import_lib(args)
```

- [ ] **Step 3: Smoke test CLI help**

```bash
cd /Users/gary/kicad-helper && python -m kicad_skill.main import-lib --help
```
Expected output includes `source_path`, `--lib-root`, `--project`, `--force`.

- [ ] **Step 4: Run all tests to verify no regressions**

```bash
cd /Users/gary/kicad-helper && python -m pytest tests/ -v --ignore=tests/test_flashlight_e2e.py
```
Expected: all pass (e2e excluded — requires KiCad CLI)

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add kicad_skill/main.py && git commit -m "feat(import-lib): wire subcommand into kicad-helper CLI"
```

---

### Task 7: Integration smoke test with real fixture

**Goal:** Verify end-to-end with the actual `ul_TPS54540DDAR` download into a temp lib-root (no touching real `~/hardwares/Libraries` or KiCad prefs).

- [ ] **Step 1: Run import against temp lib-root**

```bash
cd /Users/gary/kicad-helper && python -m kicad_skill.main import-lib \
  /Users/gary/Downloads/ul_TPS54540DDAR \
  --lib-root /tmp/kicad-test-libs \
  --project /tmp/kicad-test-libs
```

Expected output:
```
Copying ul_TPS54540DDAR → /tmp/kicad-test-libs/ul_TPS54540DDAR/KiCADv6/
  symbol:    2026-06-21_09-15-50.kicad_sym
  footprint: footprints.pretty/ (3 file(s))
Registering in project sym-lib-table... done
Registering in project fp-lib-table...  done
```

- [ ] **Step 2: Verify files on disk**

```bash
ls /tmp/kicad-test-libs/ul_TPS54540DDAR/KiCADv6/ && \
ls /tmp/kicad-test-libs/ul_TPS54540DDAR/KiCADv6/footprints.pretty/ && \
cat /tmp/kicad-test-libs/sym-lib-table && \
cat /tmp/kicad-test-libs/fp-lib-table
```

Expected: sym-lib-table and fp-lib-table contain `ul_TPS54540DDAR` entries.

- [ ] **Step 3: Run again to verify idempotent**

```bash
cd /Users/gary/kicad-helper && python -m kicad_skill.main import-lib \
  /Users/gary/Downloads/ul_TPS54540DDAR \
  --lib-root /tmp/kicad-test-libs \
  --project /tmp/kicad-test-libs \
  --force
```

Expected: `already present` for both sym and fp tables (not duplicated).

- [ ] **Step 4: Import into real library (global)**

Once smoke test passes, run the real import:

```bash
cd /Users/gary/kicad-helper && python -m kicad_skill.main import-lib \
  /Users/gary/Downloads/ul_TPS54540DDAR \
  --lib-root ~/hardwares/Libraries
```

Expected: component installed in `~/hardwares/Libraries/ul_TPS54540DDAR/` and registered in `~/Library/Preferences/kicad/10.0/sym-lib-table` + `fp-lib-table`.

- [ ] **Step 5: Commit**

```bash
cd /Users/gary/kicad-helper && git add -A && git commit -m "feat(import-lib): complete — smoke test verified"
```
