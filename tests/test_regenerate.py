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


from kicad_skill.regenerate import classify_nets


class TestClassifyNets(unittest.TestCase):
    def setUp(self):
        self.centers = {
            "U1": (0, 0), "U2": (5, 0),     # adjacent
            "U3": (200, 0),                  # far away
            "R2": (6, 0),                    # adjacent to U2
        }

    def test_power_named_net_is_label_routed(self):
        nets = [{"name": "GND", "pins": ["U1:1", "U2:1"]}]  # 2-pin, adjacent, but power
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["GND"])
        self.assertEqual(wire, [])

    def test_three_pin_net_is_label_routed(self):
        nets = [{"name": "SIG", "pins": ["U1:2", "U2:2", "R2:1"]}]
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["SIG"])

    def test_two_pin_adjacent_net_is_wire_routed(self):
        nets = [{"name": "OSC1", "pins": ["U2:7", "R2:1"]}]
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in wire], ["OSC1"])
        self.assertEqual(label, [])

    def test_two_pin_distant_net_is_label_routed(self):
        nets = [{"name": "TX", "pins": ["U1:3", "U3:1"]}]  # U1 near origin, U3 far
        label, wire = classify_nets(nets, self.centers)
        self.assertEqual([n["name"] for n in label], ["TX"])
        self.assertEqual(wire, [])
