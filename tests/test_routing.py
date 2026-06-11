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

if __name__ == "__main__":
    unittest.main()
