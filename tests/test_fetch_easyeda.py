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
            # staging/KiCADv6 still exists — copy_component only copies, never moves the source.
            with self.assertRaises(FileExistsError):
                import_fetched_component(staging, 'C2040', lib_root, table_dir, 'global', force=False)


if __name__ == '__main__':
    unittest.main()
