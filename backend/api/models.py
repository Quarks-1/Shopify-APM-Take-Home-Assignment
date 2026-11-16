from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class SpaceType(models.TextChoices):
    FLOOR = 'floor', 'Floor'
    SHELF = 'shelf', 'Shelf'
    WALL = 'wall', 'Wall'


class DimensionUnit(models.TextChoices):
    FT = 'ft', 'Feet'
    M = 'm', 'Meters'


class ObjectType(models.TextChoices):
    FURNITURE = 'furniture', 'Furniture'
    POSTER = 'poster', 'Poster'


class Material(models.TextChoices):
    METAL = 'metal', 'Metal'
    WOOD = 'wood', 'Wood'
    FABRIC = 'fabric', 'Fabric'


class Style(models.TextChoices):
    MODERN = 'modern', 'Modern'
    VINTAGE = 'vintage', 'Vintage'
    BRUTALIST = 'brutalist', 'Brutalist'


_DIMENSION_DECIMAL = {
    'max_digits': 12,
    'decimal_places': 4,
    'validators': [MinValueValidator(Decimal('0'))],
}

# Default snap step for spatial editing UIs tied to a Space (not 6 in).
EDITING_GRID_INCREMENT_INCHES = Decimal('1')


class Space(models.Model):
    x = models.DecimalField(**_DIMENSION_DECIMAL)
    y = models.DecimalField(**_DIMENSION_DECIMAL)
    z = models.DecimalField(**_DIMENSION_DECIMAL, null=True, blank=True)
    dimension_unit = models.CharField(
        max_length=8,
        choices=DimensionUnit.choices,
        default=DimensionUnit.FT,
    )
    space_type = models.CharField(max_length=32, choices=SpaceType.choices)
    weight_limit = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    editing_grid_increment_inches = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=EDITING_GRID_INCREMENT_INCHES,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text='Editing snap grid step size in inches.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']

    def __str__(self) -> str:
        unit = self.get_dimension_unit_display()
        parts = [f'x={self.x}', f'y={self.y}']
        if self.z is not None:
            parts.append(f'z={self.z}')
        dims = ' '.join(parts)
        return f'{self.get_space_type_display()} ({dims} {unit})'


class Object(models.Model):
    x = models.DecimalField(**_DIMENSION_DECIMAL)
    y = models.DecimalField(**_DIMENSION_DECIMAL)
    z = models.DecimalField(**_DIMENSION_DECIMAL, null=True, blank=True)
    weight = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0'))],
    )
    object_type = models.CharField(max_length=32, choices=ObjectType.choices)
    material = models.CharField(max_length=32, choices=Material.choices)
    style = models.CharField(max_length=32, choices=Style.choices)
    price_limit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    spaces = models.ManyToManyField(Space, related_name='child_objects', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']

    def __str__(self) -> str:
        return f'{self.get_object_type_display()} ({self.material}, {self.style})'


class SimilarItem(models.Model):
    parent_object = models.ForeignKey(
        Object,
        on_delete=models.CASCADE,
        related_name='similar_items',
    )
    name = models.CharField(max_length=255)
    image = models.URLField(max_length=2048)

    class Meta:
        ordering = ['id']

    def __str__(self) -> str:
        return self.name