import os
import unittest

from kicad_skill.netlist_eval import compare, extract_actual_netlist, load_ground_truth


GT_VDD_GND = [
    {"name": "VDD", "pins": ["U1:1", "U2:1"]},
    {"name": "GND", "pins": ["U1:2", "U2:2"]},
]


class TestCompare(unittest.TestCase):
    def test_clean_match_reports_ok_no_fatal(self):
        # Each GT net is exactly one actual electrical net.
        actual = [{"U1:1", "U2:1"}, {"U1:2", "U2:2"}]
        rep = compare(actual, GT_VDD_GND)
        self.assertEqual(rep["shorts"], [])
        self.assertEqual(rep["opens"], [])
        self.assertEqual(set(rep["ok"]), {"VDD", "GND"})
        self.assertFalse(rep["fatal"])

    def test_two_gt_nets_merged_into_one_actual_net_is_short(self):
        # VDD and GND pins all on a single electrical net -> short. This is the
        # failure the geometry-only evaluator cannot see; it is why GT eval exists.
        actual = [{"U1:1", "U2:1", "U1:2", "U2:2"}]
        rep = compare(actual, GT_VDD_GND)
        self.assertTrue(rep["fatal"])
        self.assertEqual(len(rep["shorts"]), 1)
        self.assertEqual(rep["shorts"][0]["gt_nets"], ["GND", "VDD"])
        self.assertNotIn("VDD", rep["ok"])

    def test_gt_net_split_across_actual_nets_is_open(self):
        # VDD's pins must be one net but the design left them on two -> open.
        gt = [{"name": "VDD", "pins": ["U1:1", "U2:1", "U3:1"]}]
        actual = [{"U1:1", "U2:1"}, {"U3:1"}]
        rep = compare(actual, gt)
        self.assertTrue(rep["fatal"])
        self.assertEqual(len(rep["opens"]), 1)
        self.assertEqual(rep["opens"][0]["gt_net"], "VDD")

    def test_gt_pin_absent_from_design_is_missing(self):
        actual = [{"U1:1", "U2:1"}, {"U1:2"}]  # U2:2 never placed/connected
        rep = compare(actual, GT_VDD_GND)
        self.assertIn("U2:2", rep["missing"])


class TestExtractIntegration(unittest.TestCase):
    """End-to-end: the real generated MCP2515 schematic shorts VDD<->GND.

    extract_actual_netlist must flatten parent + sub-sheet (sheet pin name tied
    to hierarchical_label name) and reproduce KiCad's electrical merge so that
    compare() reports the known short.
    """

    def setUp(self):
        base = os.path.join(os.path.dirname(__file__), "..", "scratch", "mcp_test")
        self.root = os.path.join(base, "mcp_test.kicad_sch")
        self.table = os.path.join(base, "sym-lib-table")
        self.gt = os.path.join(base, "can_node.groundtruth.json")
        if not os.path.exists(self.root) or not os.path.exists(self.gt):
            self.skipTest("mcp_test demo artifacts not present")

    def test_real_schematic_reports_vdd_gnd_short(self):
        actual = extract_actual_netlist(self.root, self.table)
        gt = load_ground_truth(self.gt)
        rep = compare(actual, gt["nets"])
        self.assertTrue(rep["fatal"], f"expected fatal; report={rep}")
        merged = {n for s in rep["shorts"] for n in s["gt_nets"]}
        self.assertIn("VDD", merged)
        self.assertIn("GND", merged)


if __name__ == "__main__":
    unittest.main()
