import json
import os
import unittest
from unittest import mock

from kicad_skill.netlist_eval import extract_actual_netlist
from kicad_skill.netlist_svg import build_yosys_netlist


class TestBuildYosysNetlist(unittest.TestCase):
    def setUp(self):
        base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.schematic = os.path.join(base, "mcp_test.kicad_sch")
        self.table = os.path.join(base, "sym-lib-table")

    def test_every_pin_appears_as_a_cell_port(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]
        actual_nets = extract_actual_netlist(self.schematic, self.table)

        expected_refs = {pid.split(":")[0] for net in actual_nets for pid in net}
        self.assertEqual(set(module["cells"].keys()), expected_refs)

        for net in actual_nets:
            for pid in net:
                ref, num = pid.split(":")
                self.assertIn(num, module["cells"][ref]["connections"])

    def test_multi_pin_nets_become_netnames_single_pin_nets_do_not(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]
        actual_nets = extract_actual_netlist(self.schematic, self.table)

        multi_pin_count = sum(1 for net in actual_nets if len(net) >= 2)
        self.assertEqual(len(module["netnames"]), multi_pin_count)

    def test_known_vdd_gnd_short_collapses_to_one_netname(self):
        # can_node fixture is a known-bad schematic: VDD and GND pins land on
        # a single electrical net (see tests/test_netlist_eval.py). The SVG
        # netlist must reproduce that merge, not silently split it.
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]

        vdd_gnd_pins = {"U1:5", "U1:6"}  # one VDD pin, one GND pin on MCU
        bits_seen = set()
        for ref_num in vdd_gnd_pins:
            ref, num = ref_num.split(":")
            for conn in module["cells"][ref]["connections"][num]:
                bits_seen.add(conn)
        # If they were still shorted, both pins resolve to the SAME net id.
        self.assertEqual(len(bits_seen), 1)

    def test_connection_ids_match_between_cell_and_netname(self):
        netlist = build_yosys_netlist(self.schematic, self.table)
        module = netlist["modules"]["top"]

        all_netname_bits = {bit for nn in module["netnames"].values() for bit in nn["bits"]}
        all_cell_bits = {
            bit
            for cell in module["cells"].values()
            for bits in cell["connections"].values()
            for bit in bits
        }
        self.assertTrue(all_netname_bits.issubset(all_cell_bits))


class TestRenderNetlistSvg(unittest.TestCase):
    def setUp(self):
        base = os.path.join(os.path.dirname(__file__), "fixtures", "can_node")
        self.schematic = os.path.join(base, "mcp_test.kicad_sch")
        self.table = os.path.join(base, "sym-lib-table")

    @mock.patch("kicad_skill.netlist_svg.subprocess.run")
    def test_invokes_npx_netlistsvg_and_cleans_up_temp_json(self, mock_run):
        from kicad_skill.netlist_svg import render_netlist_svg

        written_json = {}
        original_run = mock_run.side_effect

        def capture_and_check(cmd, check):
            self.assertEqual(cmd[:3], ["npx", "--yes", "netlistsvg"])
            self.assertEqual(cmd[-2], "-o")
            tmp_path = cmd[3]
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path) as f:
                written_json.update(json.load(f))

        mock_run.side_effect = capture_and_check

        render_netlist_svg(self.schematic, "/tmp/does_not_matter.svg", self.table)

        self.assertIn("modules", written_json)
        # temp file must be removed after the call, regardless of side_effect
        tmp_path = mock_run.call_args[0][0][3]
        self.assertFalse(os.path.exists(tmp_path))


if __name__ == "__main__":
    unittest.main()
