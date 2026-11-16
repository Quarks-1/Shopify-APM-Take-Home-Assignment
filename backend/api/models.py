from django.db import models


class SpaceType(models.TextChoices):
    FLOOR = 'floor'
    SHELF = 'shelf'
    WALL = 'wall'
    OTHER = 'other'


class LengthUnit(models.TextChoices):
    IN = 'in'
    FT = 'ft'
    CM = 'cm'


class ObjectType(models.TextChoices):
    FURNITURE = 'furniture'
    LIGHTING = 'lighting'
    RUG = 'rug'
    WALL_ART = 'wall-art'
    STORAGE = 'storage'
    DECOR = 'decor'


class Material(models.TextChoices):
    METAL = 'metal'
    WOOD = 'wood'
    FABRIC = 'fabric'
    GLASS = 'glass'
    CERAMIC = 'ceramic'
    MIXED = 'mixed'


class Style(models.TextChoices):
    MODERN = 'modern'
    VINTAGE = 'vintage'
    BRUTALIST = 'brutalist'
    SCANDINAVIAN = 'scandinavian'
    INDUSTRIAL = 'industrial'
    MID_CENTURY = 'mid-century'


class ObjectSource(models.TextChoices):
    SEARCH = 'search'
    IMPORTED = 'imported'


def default_search():
    return {'status': 'idle'}


class Space(models.Model):
    """All lengths in inches, weights in lb."""

    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=16, choices=SpaceType.choices)
    unit = models.CharField(max_length=4, choices=LengthUnit.choices)
    dims = models.JSONField()
    weight_limit = models.FloatField(null=True, blank=True)
    position = models.IntegerField(default=0)
    # Set explicitly (not auto_now) so imports keep client timestamps.
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f'{self.name} ({self.id})'


class SpaceObject(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=120)
    dims = models.JSONField()
    weight = models.FloatField(null=True, blank=True)
    type = models.CharField(max_length=16, choices=ObjectType.choices)
    material = models.CharField(max_length=16, choices=Material.choices, null=True, blank=True)
    style = models.CharField(max_length=16, choices=Style.choices, null=True, blank=True)
    price_limit = models.FloatField(null=True, blank=True)
    similar_items = models.JSONField(default=list)
    source = models.CharField(
        max_length=16, choices=ObjectSource.choices, default=ObjectSource.SEARCH
    )
    owned = models.JSONField(null=True, blank=True)
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField()

    # Legacy mock-search fields, kept so imports round-trip losslessly.
    # A later phase replaces them with real search runs.
    candidate_ids = models.JSONField(default=list)
    selected_product_id = models.CharField(max_length=128, null=True, blank=True)
    search = models.JSONField(default=default_search)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f'{self.name} ({self.id})'


class Placement(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='placements')
    object = models.ForeignKey(
        SpaceObject, on_delete=models.CASCADE, related_name='placements'
    )
    x = models.FloatField()
    y = models.FloatField()

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['space', 'object'], name='unique_space_object')
        ]
