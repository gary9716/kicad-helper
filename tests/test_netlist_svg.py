import json
import os
import unittest
from unittest import mock

from kicad_skill.netlist_svg import build_yosys_netlist, render_netlist_svg

FIXTURE_GT = os.path.join(os.path.dirname(__file__), "fixtures", "can_node",
                          "can_node.groundtruth.json")


def load_gt_nets():
    with open(FIXTURE_GT, encoding="utf-8") as f:
        return json.load(f)["nets"]


class TestBuildYosysNetlist(unittest.TestCase):
    def setUp(self):
        self.nets = load_gt_nets()

    def test_every_pin_appears_as_a_cell_port(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        expected_refs = {p.split(":")[0] for n in self.nets for p in n["pins"]}
        self.assertEqual(set(module["cells"].keys()), expected_refs)
        for n in self.nets:
            for pid in n["pins"]:
                ref, num = pid.split(":")
                self.assertIn(num, module["cells"][ref]["connections"])

    def test_netnames_use_gt_names(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        gt_names = {n["name"] for n in self.nets if len(n["pins"]) >= 2}
        self.assertEqual(set(module["netnames"].keys()), gt_names)

    def test_single_pin_nets_render_port_but_no_netname(self):
        nets = [{"name": "SIG", "pins": ["U9:1", "U8:2"]},
                {"name": "NC", "pins": ["U9:3"]}]
        module = build_yosys_netlist(nets)["modules"]["top"]
        self.assertIn("3", module["cells"]["U9"]["connections"])
        self.assertEqual(module["cells"]["U9"]["connections"]["3"], [])
        self.assertEqual(set(module["netnames"]), {"SIG"})

    def test_duplicate_net_names_are_uniquified(self):
        nets = [{"name": "N", "pins": ["A:1", "B:1"]},
                {"name": "N", "pins": ["C:1", "D:1"]}]
        module = build_yosys_netlist(nets)["modules"]["top"]
        self.assertEqual(len(module["netnames"]), 2)
        self.assertIn("N", module["netnames"])

    def test_connection_ids_match_between_cell_and_netname(self):
        module = build_yosys_netlist(self.nets)["modules"]["top"]
        netname_bits = {b for nn in module["netnames"].values() for b in nn["bits"]}
        cell_bits = {b for c in module["cells"].values()
                     for bits in c["connections"].values() for b in bits}
        self.assertTrue(netname_bits.issubset(cell_bits))
        # every net's pins share exactly its bit
        by_name = {n["name"]: n["pins"] for n in self.nets if len(n["pins"]) >= 2}
        for name, pins in by_name.items():
            bit = module["netnames"][name]["bits"][0]
            for pid in pins:
                ref, num = pid.split(":")
                self.assertEqual(module["cells"][ref]["connections"][num], [bit])


class TestRenderNetlistSvg(unittest.TestCase):
    @mock.patch("kicad_skill.netlist_svg.subprocess.run")
    def test_invokes_npx_netlistsvg_and_cleans_up_temp_json(self, mock_run):
        written = {}

        def capture(cmd, check):
            self.assertEqual(cmd[:3], ["npx", "--yes", "netlistsvg"])
            self.assertEqual(cmd[-2], "-o")
            with open(cmd[3]) as f:
                written.update(json.load(f))
        mock_run.side_effect = capture

        render_netlist_svg(FIXTURE_GT, "/tmp/out.svg")

        self.assertIn("modules", written)
        self.assertIn("VDD", written["modules"]["top"]["netnames"])
        tmp = mock_run.call_args[0][0][3]
        self.assertFalse(os.path.exists(tmp))


if __name__ == "__main__":
    unittest.main()
