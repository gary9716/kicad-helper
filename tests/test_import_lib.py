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
