from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (
    EDITING_GRID_INCREMENT_INCHES,
    DimensionUnit,
    Material,
    Object,
    ObjectType,
    SimilarItem,
    Space,
    SpaceType,
    Style,
)


class SpaceModelTests(TestCase):
    def test_space_saves_dimensions_in_feet(self):
        space = Space.objects.create(
            x=Decimal('10.5000'),
            y=Decimal('8.2500'),
            z=Decimal('9.0000'),
            dimension_unit=DimensionUnit.FT,
            space_type=SpaceType.FLOOR,
            weight_limit=Decimal('500.0000'),
        )
        space.refresh_from_db()
        self.assertEqual(space.dimension_unit, DimensionUnit.FT)
        self.assertEqual(space.x, Decimal('10.5000'))
        self.assertEqual(space.y, Decimal('8.2500'))
        self.assertEqual(space.z, Decimal('9.0000'))

    def test_space_saves_dimensions_in_meters(self):
        space = Space.objects.create(
            x=Decimal('3.2000'),
            y=Decimal('2.4000'),
            dimension_unit=DimensionUnit.M,
            space_type=SpaceType.SHELF,
        )
        space.refresh_from_db()
        self.assertEqual(space.dimension_unit, DimensionUnit.M)
        self.assertIsNone(space.z)
        self.assertIsNone(space.weight_limit)

    def test_space_default_dimension_unit_is_feet(self):
        space = Space.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            space_type=SpaceType.WALL,
        )
        self.assertEqual(space.dimension_unit, DimensionUnit.FT)

    def test_default_editing_grid_increment_is_one_inch(self):
        space = Space.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            space_type=SpaceType.FLOOR,
        )
        space.refresh_from_db()
        self.assertEqual(space.editing_grid_increment_inches, EDITING_GRID_INCREMENT_INCHES)
        self.assertEqual(space.editing_grid_increment_inches, Decimal('1'))

    def test_editing_grid_increment_can_be_persisted(self):
        space = Space.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            space_type=SpaceType.SHELF,
            editing_grid_increment_inches=Decimal('2.5000'),
        )
        space.refresh_from_db()
        self.assertEqual(space.editing_grid_increment_inches, Decimal('2.5000'))

    def test_space_type_choices(self):
        for value, _label in SpaceType.choices:
            space = Space(space_type=value, x=1, y=1)
            space.full_clean()

    def test_negative_dimension_rejected(self):
        space = Space(
            x=Decimal('-1'),
            y=Decimal('1'),
            space_type=SpaceType.FLOOR,
        )
        with self.assertRaises(ValidationError):
            space.full_clean()


class ObjectModelTests(TestCase):
    def setUp(self):
        self.space_a = Space.objects.create(
            x=Decimal('12'),
            y=Decimal('10'),
            space_type=SpaceType.FLOOR,
            dimension_unit=DimensionUnit.FT,
        )
        self.space_b = Space.objects.create(
            x=Decimal('6'),
            y=Decimal('4'),
            space_type=SpaceType.SHELF,
            dimension_unit=DimensionUnit.M,
        )

    def test_object_fields_and_optional_z(self):
        obj = Object.objects.create(
            x=Decimal('2.0000'),
            y=Decimal('1.5000'),
            weight=Decimal('45.2500'),
            object_type=ObjectType.FURNITURE,
            material=Material.WOOD,
            style=Style.MODERN,
            price_limit=Decimal('1999.99'),
        )
        obj.refresh_from_db()
        self.assertIsNone(obj.z)
        self.assertEqual(obj.object_type, ObjectType.FURNITURE)
        self.assertEqual(obj.material, Material.WOOD)
        self.assertEqual(obj.style, Style.MODERN)

    def test_object_belongs_to_multiple_spaces(self):
        obj = Object.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            weight=Decimal('10'),
            object_type=ObjectType.POSTER,
            material=Material.FABRIC,
            style=Style.VINTAGE,
            price_limit=Decimal('50.00'),
        )
        obj.spaces.set([self.space_a, self.space_b])
        self.assertEqual(obj.spaces.count(), 2)
        self.assertIn(obj, self.space_a.child_objects.all())
        self.assertIn(obj, self.space_b.child_objects.all())

    def test_similar_items_have_name_and_image(self):
        obj = Object.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            weight=Decimal('5'),
            object_type=ObjectType.FURNITURE,
            material=Material.METAL,
            style=Style.BRUTALIST,
            price_limit=Decimal('300.00'),
        )
        item = SimilarItem.objects.create(
            parent_object=obj,
            name='Steel chair variant',
            image='https://example.com/images/chair.jpg',
        )
        self.assertEqual(list(obj.similar_items.values_list('name', flat=True)), [
            'Steel chair variant',
        ])
        self.assertEqual(item.image, 'https://example.com/images/chair.jpg')

    def test_deleting_object_cascades_similar_items(self):
        obj = Object.objects.create(
            x=Decimal('1'),
            y=Decimal('1'),
            weight=Decimal('5'),
            object_type=ObjectType.FURNITURE,
            material=Material.WOOD,
            style=Style.MODERN,
            price_limit=Decimal('100.00'),
        )
        SimilarItem.objects.create(
            parent_object=obj,
            name='Similar desk',
            image='https://example.com/desk.png',
        )
        obj_id = obj.pk
        obj.delete()
        self.assertFalse(SimilarItem.objects.filter(parent_object_id=obj_id).exists())