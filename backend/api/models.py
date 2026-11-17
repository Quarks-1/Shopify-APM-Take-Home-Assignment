import uuid

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
    price_min = models.FloatField(null=True, blank=True)
    price_limit = models.FloatField(null=True, blank=True)
    min_rating = models.FloatField(null=True, blank=True)
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
    # Mirrors the object's latest SearchRun; see api.searcher.object_state.
    search = models.JSONField(default=default_search)
    # The accepted search candidate the user picked.
    selected_candidate = models.ForeignKey(
        'Candidate', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

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


class RunStatus(models.TextChoices):
    QUEUED = 'queued'
    RUNNING = 'running'
    DONE = 'done'
    EMPTY = 'empty'
    FAILED = 'failed'
    CANCELED = 'canceled'


class RunStep(models.TextChoices):
    PLANNING = 'planning'
    BUILDING_QUERY = 'building-query'
    QUERYING_CATALOG = 'querying-catalog'
    RANKING = 'ranking'


class SizeStatus(models.TextChoices):
    FITS = 'fits'
    UNVERIFIED = 'unverified'
    TOO_BIG = 'too_big'


class WeightStatus(models.TextChoices):
    FITS = 'fits'
    UNVERIFIED = 'unverified'
    TOO_HEAVY = 'too_heavy'


class Verdict(models.TextChoices):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'


class AgentMode(models.TextChoices):
    CLAUDE = 'claude'
    DETERMINISTIC = 'deterministic'
    MANUAL = 'manual'  # CLI `searcher start`: no worker, never reaped


class SearchMode(models.TextChoices):
    SEARCH = 'search'  # text queries built from the object
    LIKE = 'like'  # "more like this": catalog `like` seeded by a chosen product


class SearchRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    object = models.ForeignKey(
        SpaceObject, on_delete=models.CASCADE, related_name='search_runs'
    )
    # object_to_json at creation plus "space": {type, dims}. The loop only ever
    # reads constraints from here, never from the live object.
    object_snapshot = models.JSONField()
    status = models.CharField(
        max_length=16, choices=RunStatus.choices, default=RunStatus.QUEUED
    )
    step = models.CharField(max_length=24, choices=RunStep.choices, null=True, blank=True)
    target = models.IntegerField()
    max_rounds = models.IntegerField()
    round_count = models.IntegerField(default=0)
    error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Background execution (api.searcher.runner). Each pid leads its own
    # process group, so cancel/timeout signal the whole group.
    worker_pid = models.IntegerField(null=True, blank=True)
    agent_pid = models.IntegerField(null=True, blank=True)
    agent_mode = models.CharField(max_length=16, choices=AgentMode.choices, blank=True, default='')
    agent_model = models.CharField(max_length=64, blank=True, default='')
    # claude's final JSON, minus most of the bulky `result` text.
    agent_result = models.JSONField(null=True, blank=True)
    # Why the agent was replaced by the deterministic loop, if it was.
    fallback_reason = models.TextField(blank=True, default='')

    # "More like this": the reference product and its cached photos
    # ({candidateId, productGid, variantGid, title, seller, description,
    # imageUrls, images: [{index, path, contentType, bytes}]}).
    mode = models.CharField(max_length=8, choices=SearchMode.choices, default=SearchMode.SEARCH)
    like_source = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'run {self.id} ({self.status})'


class CatalogQuery(models.Model):
    run = models.ForeignKey(SearchRun, on_delete=models.CASCADE, related_name='queries')
    round = models.IntegerField()
    strategy = models.CharField(max_length=64)
    payload = models.JSONField()
    response_meta = models.JSONField(default=dict)
    returned_count = models.IntegerField(default=0)
    new_count = models.IntegerField(default=0)
    passing_count = models.IntegerField(default=0)
    error = models.TextField(blank=True, default='')
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['round', 'pk']


class Candidate(models.Model):
    run = models.ForeignKey(SearchRun, on_delete=models.CASCADE, related_name='candidates')
    # The query that first found it.
    query = models.ForeignKey(
        CatalogQuery, on_delete=models.CASCADE, related_name='candidates'
    )
    product_gid = models.CharField(max_length=255)
    variant_gid = models.CharField(max_length=255, blank=True, default='')
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    seller_name = models.CharField(max_length=200, blank=True, default='')
    seller_domain = models.CharField(max_length=200, blank=True, default='')
    seller_url = models.TextField(blank=True, default='')
    price_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=8, blank=True, default='')
    pdp_url = models.TextField(blank=True, default='')
    checkout_url = models.TextField(blank=True, default='')
    image_url = models.TextField(blank=True, default='')
    rating_value = models.FloatField(null=True, blank=True)
    rating_count = models.IntegerField(null=True, blank=True)
    dims = models.JSONField(null=True, blank=True)
    dims_longest = models.FloatField(null=True, blank=True)
    dims_source = models.CharField(max_length=16, null=True, blank=True)
    weight_lb = models.FloatField(null=True, blank=True)
    materials = models.JSONField(default=list)
    material_text = models.CharField(max_length=300, blank=True, default='')
    specs = models.JSONField(default=list)
    size_status = models.CharField(
        max_length=16, choices=SizeStatus.choices, default=SizeStatus.UNVERIFIED
    )
    weight_status = models.CharField(
        max_length=16, choices=WeightStatus.choices, default=WeightStatus.UNVERIFIED
    )
    passes_filters = models.BooleanField(default=True)
    filter_reasons = models.JSONField(default=list)
    verdict = models.CharField(
        max_length=16, choices=Verdict.choices, default=Verdict.PENDING
    )
    verdict_reason = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['run', 'product_gid'], name='unique_run_product'
            )
        ]


class CartLine(models.Model):
    """A cart line follows its object's current pick (selected_candidate)."""

    id = models.CharField(max_length=64, primary_key=True)
    object = models.OneToOneField(
        SpaceObject, on_delete=models.CASCADE, related_name='cart_line'
    )
    qty = models.PositiveSmallIntegerField(default=1)  # 1..20
    created_at = models.DateTimeField()

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.object_id} x{self.qty} ({self.id})'


class MerchantCartStatus(models.TextChoices):
    SYNCED = 'synced'
    DIRTY = 'dirty'
    ERROR = 'error'
    UNSUPPORTED = 'unsupported'


class MerchantCart(models.Model):
    """The merchant-side UCP cart of one store (keyed by its store id)."""

    domain = models.CharField(max_length=255, primary_key=True)
    ucp_cart_id = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(
        max_length=16, choices=MerchantCartStatus.choices, default=MerchantCartStatus.DIRTY
    )
    # Bumped by every cart edit touching this store; a sync records the
    # revision it pushed so edits made mid-sync leave the store dirty.
    revision = models.IntegerField(default=0)
    synced_revision = models.IntegerField(default=0)
    synced_signature = models.CharField(max_length=64, blank=True, default='')
    # Sanitized merchant cart: currency, totals, lineItems, messages.
    snapshot = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    synced_at = models.DateTimeField(null=True, blank=True)
    unsupported_at = models.DateTimeField(null=True, blank=True)
    checkout_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['domain']

    def __str__(self):
        return f'{self.domain} ({self.status})'


class RenderStatus(models.TextChoices):
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'


class SpaceRender(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='renders')
    fingerprint = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16, choices=RenderStatus.choices, default=RenderStatus.RUNNING
    )
    error = models.TextField(blank=True, default='')
    provider = models.CharField(max_length=16, blank=True, default='')
    model = models.CharField(max_length=64, blank=True, default='')
    prompt = models.TextField(blank=True, default='')
    items = models.JSONField(default=list)  # manifest of rendered placements
    image = models.BinaryField(null=True, blank=True)
    image_mime = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
