import unittest
import sys
import os
import tempfile

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


class TestTableRegistration(unittest.TestCase):
    def test_register_symbol_adds_entry(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'sym-lib-table')
            with open(table, 'w') as f:
                f.write('(sym_lib_table\n  (version 7)\n)')
            from kicad_skill.import_lib import register_symbol
            result = register_symbol(d, 'ul_TEST', '/path/test.kicad_sym')
            self.assertTrue(result)
            with open(table) as f:
                content = f.read()
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
            with open(table) as f:
                content = f.read()
            self.assertIn('(name "ul_TEST")', content)

    def test_register_footprint_skips_if_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, 'fp-lib-table')
            with open(table, 'w') as f:
                f.write('(fp_lib_table\n  (lib (name "ul_TEST") (uri "/path/test.pretty"))\n)')
            from kicad_skill.import_lib import register_footprint
            result = register_footprint(d, 'ul_TEST', '/path/test.pretty')
            self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
