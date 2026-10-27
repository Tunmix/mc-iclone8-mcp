import math
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The IK module only needs RLPy constants at import time; a stub is enough for the math.
_stub = types.ModuleType("RLPy")
_stub.__getattr__ = lambda name: 0
sys.modules.setdefault("RLPy", _stub)

from tools import ik  # noqa: E402


class TestIkMath(unittest.TestCase):
    def assertVector(self, actual, expected):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=6)

    def test_euler_round_trip(self):
        angles = (math.radians(20), math.radians(-35), math.radians(70))
        self.assertVector(ik.quat_to_euler(ik.euler_to_quat(*angles)), angles)

    def test_euler_applies_x_then_y_then_z(self):
        # X 90 takes +Y to +Z, then Z 90 leaves +Z alone.
        q = ik.euler_to_quat(math.radians(90), 0, math.radians(90))
        self.assertVector(ik._rotate(q, [0, 1, 0]), [0, 0, 1])
        # X 90 leaves +X alone, then Z 90 takes it to +Y.
        self.assertVector(ik._rotate(q, [1, 0, 0]), [0, 1, 0])

    def test_shortest_rotation_maps_directions(self):
        for u, v in (([1, 0, 0], [0, 0, 1]), ([0, 2, 0], [0, -3, 0]), ([1, 1, 0], [1, 1, 0])):
            q = ik._q_between(u, v)
            self.assertVector(ik._rotate(q, ik._normalize(u)), ik._normalize(v))


if __name__ == "__main__":
    unittest.main()
