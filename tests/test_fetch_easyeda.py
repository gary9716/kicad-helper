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
