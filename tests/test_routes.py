"""Pásma délky trasy. Silniční vzdálenost zůstává OSRM, tady je jen výběr."""
import random
import unittest
from datetime import date
from unittest.mock import patch

from routes import BUCKETS, MAX_KM, pick_route
from scheduler import make_plan


class RouteBandTests(unittest.TestCase):
    def test_shorter_bands_stay_and_the_range_reaches_30(self):
        self.assertEqual(BUCKETS[:5], [(1, 3), (3, 6), (6, 10), (10, 15), (15, 20)])
        self.assertEqual(BUCKETS[5:], [(20, 25), (25, 30)])
        self.assertEqual(MAX_KM, 30)
        self.assertEqual(BUCKETS[0][0], 1)
        for (_, hi), (lo, _) in zip(BUCKETS, BUCKETS[1:]):
            self.assertEqual(hi, lo)

    def test_upper_band_accepts_a_road_distance_inside_it(self):
        addresses = [("A", 50.08, 14.42), ("B", 50.22, 14.42)]
        with patch("routes.ADDRESSES", addresses), patch("routes.road_km", return_value=27.4):
            origin, dest, km = pick_route(6, rng=random.Random(0))
        self.assertEqual(km, 27.4)
        self.assertEqual({origin, dest}, {"A", "B"})

    def test_fallback_accepts_30_and_rejects_anything_longer(self):
        addresses = [("A", 50.08, 14.42), ("B", 50.22, 14.42)]
        with patch("routes.ADDRESSES", addresses), patch("routes.road_km", return_value=30.0):
            _, _, km = pick_route(0, rng=random.Random(0))
        self.assertEqual(km, 30.0)
        with patch("routes.ADDRESSES", addresses), patch("routes.road_km", return_value=30.1):
            with self.assertRaises(RuntimeError):
                pick_route(0, rng=random.Random(0))

    def test_daily_plan_uses_the_bands_above_20(self):
        buckets = [slot["bucket"] for slot in make_plan(date(2026, 10, 23), n=10)]
        self.assertIn(5, buckets)
        self.assertIn(6, buckets)
        self.assertTrue(all(0 <= bucket < len(BUCKETS) for bucket in buckets))
