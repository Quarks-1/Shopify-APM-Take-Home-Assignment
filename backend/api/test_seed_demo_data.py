from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from .models import Object, Space, SpaceType


class SeedDemoDataTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_demo_data")
        self.assertEqual(Space.objects.count(), 2)
        self.assertEqual(Object.objects.count(), 3)
        call_command("seed_demo_data")
        self.assertEqual(Space.objects.count(), 2)
        self.assertEqual(Object.objects.count(), 3)

    def test_seed_creates_floor_with_objects(self):
        call_command("seed_demo_data")
        floor = Space.objects.get(space_type=SpaceType.FLOOR)
        self.assertEqual(floor.x, Decimal("24"))
        placed = Object.objects.filter(spaces=floor)
        self.assertGreaterEqual(placed.count(), 2)