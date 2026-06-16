import os
import unittest

from kicad_skill.regenerate import regenerate_schematic
from kicad_skill.evaluate_layout import evaluate_schematic_layout


class TestEvaluateLayoutLabelAwareness(unittest.TestCase):
    """A pin joined to its net by a local label (not a wire) is connected.

    The regenerator label-routes most nets, placing a label exactly on each pin.
    evaluate_layout must treat such a pin as connected; otherwise it reports
    dozens of false 'disconnected pin' deductions on an electrically-correct
    schematic (netlist_eval certifies 0 opens).
    """

    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "..", "scratch", "mcp_test")
        self.table = os.path.join(self.base, "sym-lib-table")
        self.gt = os.path.join(self.base, "can_node.groundtruth.json")
        if not os.path.exists(self.table) or not os.path.exists(self.gt):
            self.skipTest("mcp_test artifacts not present")
        self.out = os.path.join(self.base, "_eval_label_test.kicad_sch")
        self.addCleanup(lambda: os.path.exists(self.out) and os.remove(self.out))

    def test_label_connected_pins_are_not_reported_disconnected(self):
        regenerate_schematic(self.gt, self.table, self.out)
        res = evaluate_schematic_layout(self.out, self.table)
        self.assertEqual(
            res["unconnected_pins_count"], 0,
            f"label-connected pins wrongly flagged: "
            f"{[i for i in res['issues'] if 'DISCONNECT' in i][:5]}",
        )


if __name__ == "__main__":
    unittest.main()
