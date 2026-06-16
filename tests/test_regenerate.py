import json
import os
import unittest

from kicad_skill.regenerate import load_gt_components


class TestLoadGtComponents(unittest.TestCase):
    def test_missing_component_for_net_pin_raises(self):
        gt = {
            "nets": [{"name": "VDD", "pins": ["U1:1", "U9:1"]}],
            "components": {"U1": {"lib_id": "x:Y", "value": "v"}},
        }
        with self.assertRaises(ValueError) as cm:
            load_gt_components(gt)
        self.assertIn("U9", str(cm.exception))

    def test_valid_gt_returns_nets_and_components(self):
        gt = {
            "nets": [{"name": "VDD", "pins": ["U1:1", "U2:1"]}],
            "components": {
                "U1": {"lib_id": "x:Y", "value": "v"},
                "U2": {"lib_id": "x:Z", "value": "w"},
            },
        }
        nets, comps = load_gt_components(gt)
        self.assertEqual(len(nets), 1)
        self.assertEqual(set(comps), {"U1", "U2"})
