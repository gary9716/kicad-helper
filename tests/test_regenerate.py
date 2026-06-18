import json
import os
import unittest

from kicad_skill.regenerate import load_gt_components
from kicad_skill.schematic import place_symbols_and_resolve
from kicad_skill.netlist_eval import extract_actual_netlist, compare


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
        # U1/U2/U3 are ICs; R2 is a passive; J1 a connector.
        self.components = {
            "U1": {"lib_id": "lib:MCU"}, "U2": {"lib_id": "lib:CHIP"},
            "U3": {"lib_id": "lib:CHIP"}, "R2": {"lib_id": "Device:R"},
            "J1": {"lib_id": "Connector_Generic:Conn_01x04"},
        }
        self.centers = {
            "U1": (0, 0), "U2": (5, 0),     # adjacent
            "U3": (200, 0),                  # far away
            "R2": (6, 0),                    # adjacent to U2
            "J1": (7, 0),                    # adjacent to U2
        }

    def cls(self, nets):
        return classify_nets(nets, self.components, self.centers)

    def test_power_named_net_is_label_routed(self):
        nets = [{"name": "GND", "pins": ["U2:1", "R2:1"]}]  # touches passive but power
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in label], ["GND"])
        self.assertEqual(wire, [])

    def test_ic_to_ic_adjacent_net_is_label_routed(self):
        nets = [{"name": "SPI", "pins": ["U1:2", "U2:2"]}]  # both ICs, adjacent
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in label], ["SPI"])
        self.assertEqual(wire, [])

    def test_ic_to_passive_adjacent_net_is_wire_routed(self):
        nets = [{"name": "RESET", "pins": ["U2:7", "R2:1"]}]  # IC<->passive, adjacent
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in wire], ["RESET"])
        self.assertEqual(label, [])

    def test_ic_to_connector_adjacent_net_is_wire_routed(self):
        nets = [{"name": "CANH", "pins": ["U2:3", "J1:3"]}]  # IC<->connector, adjacent
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in wire], ["CANH"])

    def test_three_pin_local_passive_net_is_wire_routed(self):
        # A multi-pin local cluster touching a passive is wired directly.
        nets = [{"name": "SIG", "pins": ["U1:2", "U2:2", "R2:1"]}]
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in wire], ["SIG"])

    def test_three_pin_ic_only_net_is_label_routed(self):
        # No passive/connector in the net -> label even if local.
        nets = [{"name": "BUS", "pins": ["U1:2", "U2:2", "U2:5"]}]
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in label], ["BUS"])

    def test_two_pin_distant_net_is_label_routed(self):
        nets = [{"name": "TX", "pins": ["U2:3", "U3:1"]}]  # adjacent? U3 far -> label
        label, wire = self.cls(nets)
        self.assertEqual([n["name"] for n in label], ["TX"])
        self.assertEqual(wire, [])


from kicad_skill.regenerate import _label_orientation


class TestLabelOrientation(unittest.TestCase):
    """Label text must radiate OUTWARD from the symbol body so it does not overlap
    the body or neighbouring pin text. The anchor stays on the pin (KiCad needs
    the exact pin coordinate to connect); only angle + justify change.
    """

    def test_left_side_pin_text_grows_left(self):
        # pin left of center -> horizontal label, text grows left (justify right)
        self.assertEqual(_label_orientation(0.0, 5.0, 10.0, 5.0), (0, "right"))

    def test_right_side_pin_text_grows_right(self):
        self.assertEqual(_label_orientation(20.0, 5.0, 10.0, 5.0), (0, "left"))

    def test_top_side_pin_text_grows_up(self):
        # pin above center (smaller y, KiCad Y-down) -> vertical label growing up
        self.assertEqual(_label_orientation(5.0, 0.0, 5.0, 10.0), (90, "left"))

    def test_bottom_side_pin_text_grows_down(self):
        self.assertEqual(_label_orientation(5.0, 20.0, 5.0, 10.0), (90, "right"))


import tempfile
import shutil
from kicad_skill.regenerate import (
    _write_blank_schematic, _pin_coords, _emit_labels,
)


class TestLabelEmission(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.table = os.path.join(self.base, "sym-lib-table")
        if not os.path.exists(self.table):
            self.skipTest("mcp_test artifacts not present")
        self.tmp = tempfile.mkdtemp()
        # work inside the project dir so ${KIPRJMOD} relative libs resolve
        self.sch = os.path.join(self.base, "_tmp_label_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.sch) and os.remove(self.sch))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_labels_join_two_pins_into_one_net(self):
        _write_blank_schematic(self.sch)
        place_symbols_and_resolve(self.sch, self.table, [
            {"lib_id": "mcp_test:MCU", "reference": "U1", "value": "MCU",
             "x": 80, "y": 80, "angle": 0.0},
            {"lib_id": "mcp_test:MCP2515", "reference": "U2", "value": "MCP2515",
             "x": 140, "y": 80, "angle": 0.0},
        ], margin=2.54, resolve=True)
        coords = _pin_coords(self.sch, self.table)
        # U1:5 (3V3) and U2:18 (VDD) should both exist
        self.assertIn(("U1", "5"), coords)
        self.assertIn(("U2", "18"), coords)
        _emit_labels(self.sch, [{"name": "VDD", "pins": ["U1:5", "U2:18"]}], coords)
        actual = extract_actual_netlist(self.sch, self.table)
        rep = compare(actual, [{"name": "VDD", "pins": ["U1:5", "U2:18"]}])
        self.assertFalse(rep["fatal"], rep)
        self.assertEqual(rep["opens"], [])


from kicad_skill.regenerate import regenerate_schematic


class TestRegenerateIntegration(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.table = os.path.join(self.base, "sym-lib-table")
        self.gt = os.path.join(self.base, "can_node.groundtruth.json")
        if not os.path.exists(self.table) or not os.path.exists(self.gt):
            self.skipTest("mcp_test artifacts not present")
        self.out = os.path.join(self.base, "_regen_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.out) and os.remove(self.out))

    def test_regenerated_schematic_has_no_short_or_open(self):
        out_path, rep = regenerate_schematic(self.gt, self.table, self.out)
        self.assertFalse(rep["fatal"], f"report still fatal: {rep}")
        self.assertEqual(rep["shorts"], [])
        self.assertEqual(rep["opens"], [])

    def test_regenerated_schematic_is_flat(self):
        regenerate_schematic(self.gt, self.table, self.out)
        with open(self.out) as f:
            content = f.read()
        self.assertNotIn("(sheet ", content)
