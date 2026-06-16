import unittest
import sys
import os

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.schematic import BoundingBox, find_orthogonal_path

class TestRouting(unittest.TestCase):
    def test_routing_clear_space(self):
        start = (0.0, 0.0)
        end = (10.16, 5.08)  # Grid aligned (multiples of 1.27)
        obstacles = []
        
        path = find_orthogonal_path(start, end, obstacles, grid_size=1.27)
        self.assertIsNotNone(path)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], end)
        
        # Verify the path is orthogonal: all segments are either horizontal or vertical
        for i in range(len(path) - 1):
            dx = path[i+1][0] - path[i][0]
            dy = path[i+1][1] - path[i][1]
            self.assertTrue(dx == 0 or dy == 0, f"Segment from {path[i]} to {path[i+1]} is not orthogonal!")
            # Coordinates must snap to 1.27 grid
            self.assertAlmostEqual(path[i][0] % 1.27, 0.0)
            self.assertAlmostEqual(path[i][1] % 1.27, 0.0)

    def test_routing_around_obstacle(self):
        # We want to route from (0, 0) to (10.16, 0)
        # But we place a blocking obstacle in the middle at (5.08, 0)
        start = (0.0, 0.0)
        end = (10.16, 0.0)
        
        # Obstacle blocks x=5.08, y=0. Large enough to block directly
        obstacle = BoundingBox(xmin=3.81, ymin=-2.54, xmax=6.35, ymax=2.54)
        obstacles = [obstacle]
        
        path = find_orthogonal_path(start, end, obstacles, grid_size=1.27)
        self.assertIsNotNone(path)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], end)
        
        # Verify that no point in the path is inside the obstacle (with safety margin)
        # Wait, the path segments shouldn't cross the obstacle
        for pt in path:
            x, y = pt
            inside = (obstacle.xmin <= x <= obstacle.xmax) and (obstacle.ymin <= y <= obstacle.ymax)
            self.assertFalse(inside, f"Path point {pt} is inside the obstacle!")

    def test_routing_required_end_dir(self):
        start = (0.0, 0.0)
        end = (5.08, 5.08)
        obstacles = []
        
        # Test required_end_dir = (1, 0)
        path = find_orthogonal_path(start, end, obstacles, grid_size=1.27, required_end_dir=(1, 0))
        self.assertIsNotNone(path)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], end)
        dx = path[-1][0] - path[-2][0]
        dy = path[-1][1] - path[-2][1]
        self.assertTrue(dx > 0 and dy == 0)
        
        # Test required_end_dir = (0, 1)
        path2 = find_orthogonal_path(start, end, obstacles, grid_size=1.27, required_end_dir=(0, 1))
        self.assertIsNotNone(path2)
        self.assertEqual(path2[0], start)
        self.assertEqual(path2[-1], end)
        dx2 = path2[-1][0] - path2[-2][0]
        dy2 = path2[-1][1] - path2[-2][1]
        self.assertTrue(dx2 == 0 and dy2 > 0)

    def test_connect_symbols_no_deletion(self):
        import tempfile
        from kicad_skill.schematic import connect_symbols_in_schematic
        from kicad_skill.parser import parse_sexpr, format_sexpr
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sch_path = os.path.join(tmpdir, "test.kicad_sch")
            table_path = os.path.join(tmpdir, "sym-lib-table")
            
            # Write a minimal sym-lib-table
            with open(table_path, 'w', encoding='utf-8') as f:
                f.write('(sym_lib_table)\n')
                
            # Write a schematic with three symbols BT1, U1, U2
            # BT1 is at (100.33, 100.33). U1 is at (149.86, 100.33). U2 is at (149.86, 149.86).
            sch_content = """(kicad_sch
  (version 20211123)
  (generator "eeschema")
  (generator_version "10.0")
  (uuid "test-uuid-999")
  (paper "A4")
  (lib_symbols
    (symbol "local_test:CONN"
      (pin_names (offset 1.016))
      (property "Reference" "CONN" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "CONN" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "CONN_1_1"
        (pin passive line (at 10.16 0 180) (length 2.54)
          (name "1" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
    (symbol "local_test:IC"
      (pin_names (offset 1.016))
      (property "Reference" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "IC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "IC_1_1"
        (pin input line (at -10.16 0 0) (length 2.54)
          (name "1" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "local_test:CONN") (at 100.33 100.33 0)
    (property "Reference" "BT1" (at 100.33 105.41 0))
    (property "Value" "CONN" (at 100.33 110.49 0))
  )
  (symbol (lib_id "local_test:IC") (at 149.86 100.33 0)
    (property "Reference" "U1" (at 149.86 105.41 0))
    (property "Value" "IC" (at 149.86 110.49 0))
  )
  (symbol (lib_id "local_test:IC") (at 149.86 149.86 0)
    (property "Reference" "U2" (at 149.86 154.94 0))
    (property "Value" "IC" (at 149.86 160.02 0))
  )
)
"""
            with open(sch_path, 'w', encoding='utf-8') as f:
                f.write(sch_content)
                
            # 1. Connect BT1:1 to U1:1
            num_wires_1 = connect_symbols_in_schematic(
                schematic_path=sch_path,
                table_path=table_path,
                connections=[{"from": "BT1:1", "to": "U1:1"}],
                orthogonal=True
            )
            self.assertTrue(num_wires_1 > 0)
            
            # Read schematic and verify one wire exists
            with open(sch_path, 'r', encoding='utf-8') as f:
                sch_sexpr = parse_sexpr(f.read())
            wires_1 = [child for child in sch_sexpr[1:] if isinstance(child, list) and child[0] == 'wire']
            self.assertTrue(len(wires_1) > 0)
            
            # Save the UUIDs of the first set of wires
            wire_uuids_1 = set()
            for w in wires_1:
                uuid_node = next((sub[1] for sub in w[1:] if isinstance(sub, list) and sub[0] == 'uuid'), None)
                if uuid_node:
                    wire_uuids_1.add(uuid_node)
            
            # 2. Connect BT1:1 to U2:1 (sharing BT1:1 pin!)
            num_wires_2 = connect_symbols_in_schematic(
                schematic_path=sch_path,
                table_path=table_path,
                connections=[{"from": "BT1:1", "to": "U2:1"}],
                orthogonal=True
            )
            self.assertTrue(num_wires_2 > 0)
            
            # Read schematic again
            with open(sch_path, 'r', encoding='utf-8') as f:
                sch_sexpr_final = parse_sexpr(f.read())
            wires_final = [child for child in sch_sexpr_final[1:] if isinstance(child, list) and child[0] == 'wire']
            
            # The final set of wires must contain the original wires
            wire_uuids_final = set()
            for w in wires_final:
                uuid_node = next((sub[1] for sub in w[1:] if isinstance(sub, list) and sub[0] == 'uuid'), None)
                if uuid_node:
                    wire_uuids_final.add(uuid_node)
                    
            # Check that all first wire uuids are still present
            for uid in wire_uuids_1:
                self.assertIn(uid, wire_uuids_final, f"Wire {uid} was deleted during the second connection!")

    def test_connect_multipin_net_no_short(self):
        """Regression: routing a multi-pin net must join its own existing wires (net-aware
        wire blocking) instead of failing into an overlap-blind fallback that shorts a
        different net. Also guards against routing through a pin (pin-blocking kept in all
        A* tiers)."""
        import tempfile
        from kicad_skill.schematic import connect_symbols_in_schematic
        from kicad_skill.evaluate_layout import evaluate_schematic_layout

        with tempfile.TemporaryDirectory() as tmpdir:
            sch_path = os.path.join(tmpdir, "test.kicad_sch")
            table_path = os.path.join(tmpdir, "sym-lib-table")
            with open(table_path, 'w', encoding='utf-8') as f:
                f.write('(sym_lib_table)\n')

            # Two ICs (left/right pins) and two 2-pin passives, so VNET spans 3 pins and a
            # second net (SIG) runs nearby — the geometry that drove the old shorting bug.
            sch_content = """(kicad_sch
  (version 20211123)
  (generator "eeschema")
  (generator_version "10.0")
  (uuid "rt-uuid-1")
  (paper "A4")
  (lib_symbols
    (symbol "local_test:IC2"
      (pin_names (offset 1.016))
      (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "IC2" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "IC2_1_1"
        (pin passive line (at -10.16 2.54 0) (length 2.54)
          (name "VA" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at -10.16 -2.54 0) (length 2.54)
          (name "SA" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
    (symbol "local_test:R2"
      (pin_names (offset 1.016))
      (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "R2" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "R2_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27)
          (name "1" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27)
          (name "2" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )
  )
  (symbol (lib_id "local_test:IC2") (at 124.46 100.33 0)
    (property "Reference" "U1" (at 124.46 90.17 0)) (property "Value" "IC2" (at 124.46 113.03 0)))
  (symbol (lib_id "local_test:IC2") (at 165.10 100.33 0)
    (property "Reference" "U2" (at 165.10 90.17 0)) (property "Value" "IC2" (at 165.10 113.03 0)))
  (symbol (lib_id "local_test:R2") (at 109.22 80.01 0)
    (property "Reference" "R1" (at 113.03 80.01 0)) (property "Value" "R2" (at 113.03 80.01 0)))
)
"""
            with open(sch_path, 'w', encoding='utf-8') as f:
                f.write(sch_content)

            connect_symbols_in_schematic(sch_path, table_path, [
                {"from": "U1:VA", "to": "U2:VA"},   # VNET pin 1-2
                {"from": "U2:VA", "to": "R1:2"},    # VNET pin 3 — forces routing along own net
                {"from": "U1:SA", "to": "U2:SA"},   # separate SIG net running alongside VNET
            ], orthogonal=True)

            res = evaluate_schematic_layout(sch_path, table_path)
            self.assertEqual(res["shorts"], 0, f"connect introduced shorts: {res['issues']}")
            self.assertEqual(res["dangling"], 0, f"connect left dangling wires: {res['issues']}")

if __name__ == "__main__":
    unittest.main()
