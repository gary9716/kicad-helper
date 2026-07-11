import unittest
import unittest.mock
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
        from kicad_skill.import_lib import _resolve_table_scope
        with unittest.mock.patch('kicad_skill.import_lib._find_global_table_dir', return_value='/fake/global/dir'):
            table_dir, scope = _resolve_table_scope(None)
        self.assertEqual(table_dir, '/fake/global/dir')
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


if __name__ == '__main__':
    unittest.main()
