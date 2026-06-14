import unittest
import sys
import os

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.symbol import generate_symbol_sexpr
from kicad_skill.schematic import BoundingBox, get_instance_aabb

class TestSymbolGeometry(unittest.TestCase):
    def test_symbol_generation_grid_snapping(self):
        pins = [
            {"side": "left", "number": "1", "name": "VCC", "type": "power_in"},
            {"side": "right", "number": "2", "name": "GND", "type": "power_in"},
        ]
        
        # spacing is 2.54, so body width/height should snap to multiples of 2.54
        symbol = generate_symbol_sexpr("TEST_SNAP", pins, width=10.0, height=10.0)
        self.assertEqual(symbol[0], "symbol")
        self.assertEqual(symbol[1], "TEST_SNAP")
        
        # Check that rectangle coordinates are snapped to 2.54 grid
        body_rect = None
        for item in symbol:
            if isinstance(item, list) and item[0] == "symbol" and item[1] == "TEST_SNAP_0_1":
                for child in item:
                    if isinstance(child, list) and child[0] == "rectangle":
                        body_rect = child
                        break
        
        self.assertIsNotNone(body_rect)
        start = [float(x) for x in body_rect[1][1:]]
        end = [float(x) for x in body_rect[2][1:]]
        
        # Dimensions must snap to multiples of 2.54
        self.assertEqual(start[0] % 2.54, 0.0)
        self.assertEqual(start[1] % 2.54, 0.0)
        self.assertEqual(end[0] % 2.54, 0.0)
        self.assertEqual(end[1] % 2.54, 0.0)

    def test_symbol_body_expansion_with_long_pin_names(self):
        pins_short = [
            {"side": "left", "number": "1", "name": "A"},
            {"side": "right", "number": "2", "name": "B"},
        ]
        pins_long = [
            {"side": "left", "number": "1", "name": "VERY_LONG_PIN_NAME_1"},
            {"side": "right", "number": "2", "name": "VERY_LONG_PIN_NAME_2"},
        ]
        
        sym_short = generate_symbol_sexpr("SHORT", pins_short, width=5.08)
        sym_long = generate_symbol_sexpr("LONG", pins_long, width=5.08)
        
        # Extract width of short body
        short_width = 0
        for item in sym_short:
            if isinstance(item, list) and item[0] == "symbol" and item[1] == "SHORT_0_1":
                rect = item[2]
                short_width = float(rect[2][1]) - float(rect[1][1])
                
        # Extract width of long body
        long_width = 0
        for item in sym_long:
            if isinstance(item, list) and item[0] == "symbol" and item[1] == "LONG_0_1":
                rect = item[2]
                long_width = float(rect[2][1]) - float(rect[1][1])
                
        self.assertTrue(long_width > short_width, f"Long symbol width ({long_width}) should be larger than short symbol width ({short_width})")

    def test_instance_aabb_transformations(self):
        local_bbox = BoundingBox(xmin=-10, ymin=-5, xmax=10, ymax=5)
        
        # Test translation only
        global_bbox = get_instance_aabb(local_bbox, tx=100, ty=200, angle=0)
        self.assertEqual(global_bbox.xmin, 90)
        self.assertEqual(global_bbox.xmax, 110)
        self.assertEqual(global_bbox.ymin, 195)
        self.assertEqual(global_bbox.ymax, 205)

        # Test rotation (90 degrees swaps width and height boundaries)
        global_bbox_90 = get_instance_aabb(local_bbox, tx=100, ty=200, angle=90)
        self.assertAlmostEqual(global_bbox_90.xmin, 95)
        self.assertAlmostEqual(global_bbox_90.xmax, 105)
        self.assertAlmostEqual(global_bbox_90.ymin, 190)
        self.assertAlmostEqual(global_bbox_90.ymax, 210)

    def test_invalid_pin_type(self):
        pins = [
            {"side": "left", "number": "1", "name": "VCC", "type": "power_in_invalid_type"}
        ]
        with self.assertRaises(ValueError):
            generate_symbol_sexpr("TEST_INVALID", pins)

    def test_miso_pin_output_conflict(self):
        pins = [
            {"side": "left", "number": "1", "name": "SPI_MISO", "type": "output"}
        ]
        with self.assertRaises(ValueError):
            generate_symbol_sexpr("TEST_MISO_CONF", pins)

        # Should pass if type is tri_state
        pins_ok = [
            {"side": "left", "number": "1", "name": "SPI_MISO", "type": "tri_state"}
        ]
        symbol = generate_symbol_sexpr("TEST_MISO_OK", pins_ok)
        self.assertEqual(symbol[1], "TEST_MISO_OK")

if __name__ == "__main__":
    unittest.main()
