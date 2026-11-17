"""primary_type: one ObjectType group per pin, picked from the item noun."""
from django.test import SimpleTestCase

from .vibes.primary_type import primary_type

CASES = [
    ('table lamp', [], 'lighting'),
    ('bed', [], 'furniture'),
    ('platform bed', [], 'furniture'),
    ('coffee table book', [], 'decor'),
    ('display cabinet', [], 'storage'),
    ('shelving unit', [], 'storage'),
    ('hanging plant', [], 'decor'),
    ('planter', [], 'decor'),
    ('framed print', [], 'wall-art'),
    ('wall poster', [], 'wall-art'),
    ('kilim rug', [], 'rug'),
    ('doormat', [], 'rug'),
    ('taper candles', [], 'decor'),
    ('string lights', [], 'lighting'),
    ('pendant chandelier', [], 'lighting'),
    ('glass vase', [], 'decor'),
    ('dinnerware set', [], 'decor'),
    ('Floor-Lamp', [], 'lighting'),
    ('zzz', ['furniture', 'decor'], 'decor'),
    ('zzz', ['lighting'], 'lighting'),
    ('', [], 'other'),
]


class PrimaryTypeTests(SimpleTestCase):
    def test_table(self):
        for item, cats, expected in CASES:
            with self.subTest(item=item, cats=cats):
                self.assertEqual(primary_type(item, cats), expected)
