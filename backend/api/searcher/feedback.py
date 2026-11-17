"""Thumbs votes as search feedback.

Every run drops disliked products (any row). A refine run ("Refresh")
snapshots the row's votes, what the row has already shown and a few votes
from other rows (soft taste hints) into SearchRun.feedback at creation, and
carries the row's liked products over as pinned candidates.
"""
import math

from django.conf import settings
from django.db.models import Q

from ..models import (
    PINNED_STRATEGY, Candidate, CatalogQuery, ProductVote, RunStatus, SearchRun, Verdict,
    VoteChoice,
)
from . import filters

FINISHED = (RunStatus.DONE, RunStatus.EMPTY)
TASTE_CAP = 8  # per vote direction
TITLE_LEN = 160
DESCRIPTION_LEN = 300
MAX_MATERIALS = 6
# Fields a pinned clone does not copy from its source candidate.
CLONE_SKIP = {'id', 'run', 'query', 'verdict', 'verdict_reason', 'created_at'}


def disliked_gids():
    """Every disliked product, whichever row it was voted down in."""
    return set(
        ProductVote.objects.filter(vote=VoteChoice.DOWN).values_list('product_gid', flat=True)
    )


def has_finished_run(space_object):
    return SearchRun.objects.filter(object_id=space_object.pk, status__in=FINISHED).exists()


def _price(value):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok and value > 0 else None


def _text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _materials(product):
    items = product.get('materials')
    if not isinstance(items, list):
        return []
    return [_text(m, 60) for m in items if isinstance(m, str) and m.strip()][:MAX_MATERIALS]


def _item(vote):
    """A same-row vote with its product snapshot (merchant data)."""
    p = vote.product if isinstance(vote.product, dict) else {}
    seller = p.get('seller') if isinstance(p.get('seller'), dict) else {}
    dims = p.get('dims') if isinstance(p.get('dims'), dict) else None
    return {
        'productGid': vote.product_gid,
        'vote': vote.vote,
        'title': _text(p.get('title'), TITLE_LEN),
        'seller': _text(seller.get('name'), 100) or None,
        'price': _price(p.get('price')),
        'materials': _materials(p),
        'dims': dims,
        'description': _text(p.get('description'), DESCRIPTION_LEN) or None,
        'imageUrl': p.get('imageUrl') if isinstance(p.get('imageUrl'), str) else None,
    }


def _taste_item(vote):
    """A vote from another row: slim, a soft hint only."""
    p = vote.product if isinstance(vote.product, dict) else {}
    out = {
        'title': _text(p.get('title'), TITLE_LEN),
        'materials': _materials(p),
        'price': _price(p.get('price')),
        'vote': vote.vote,
    }
    if vote.object is not None:
        out['row'] = _text(vote.object.name, 80)
    return out


def _taste(space_object):
    others = (
        ProductVote.objects.filter(~Q(object_id=space_object.pk) | Q(object__isnull=True))
        .select_related('object').order_by('-updated_at', '-pk')
    )
    out = []
    for vote in (VoteChoice.UP, VoteChoice.DOWN):
        out += [_taste_item(v) for v in others.filter(vote=vote)[:TASTE_CAP]]
    return out


def build_feedback(space_object):
    """The refine snapshot: this row's liked/disliked items (newest vote
    first), other rows' votes as taste hints, and every product this row
    has already shown (accepted candidates of its earlier runs)."""
    mine = list(ProductVote.objects.filter(object_id=space_object.pk).order_by('-updated_at', '-pk'))
    seen = (
        Candidate.objects.filter(run__object_id=space_object.pk, verdict=Verdict.ACCEPTED)
        .values_list('product_gid', flat=True).distinct()
    )
    return {
        'liked': [_item(v) for v in mine if v.vote == VoteChoice.UP],
        'disliked': [_item(v) for v in mine if v.vote == VoteChoice.DOWN],
        'taste': _taste(space_object),
        'seen': sorted(set(seen)),
        'pinned': [],
        'images': [],
    }


def _clone(source, run, query, snapshot):
    """An accepted copy of `source` in `run`, re-checked against the run's
    snapshot (the row may have changed since). None when it no longer passes."""
    fields = {
        f.attname: getattr(source, f.attname)
        for f in Candidate._meta.concrete_fields if f.name not in CLONE_SKIP
    }
    passes, reasons, size_status, weight_status = filters.evaluate(
        fields, snapshot, settings.SEARCHER_SIZE_TOLERANCE
    )
    if not passes:
        return None
    fields.update(passes_filters=True, filter_reasons=reasons, size_status=size_status,
                  weight_status=weight_status)
    return Candidate(run=run, query=query, verdict=Verdict.ACCEPTED, **fields)


def pin_liked(run, space_object):
    """Clone the row's liked candidates (newest vote first) into `run` as
    accepted pins. The selected product is not cloned: the client keeps it
    first already (like "more like this"). Returns the pinned gids."""
    feedback = dict(run.feedback or {})
    selected_gid = (
        Candidate.objects.filter(pk=space_object.selected_candidate_id)
        .values_list('product_gid', flat=True).first()
    )
    sources = []
    for item in feedback.get('liked') or []:
        gid = item.get('productGid')
        if not gid or gid == selected_gid:
            continue
        cand = (
            Candidate.objects.filter(run__object_id=space_object.pk, product_gid=gid)
            .exclude(run=run).order_by('-pk').first()
        )
        if cand is not None:
            sources.append(cand)
    pinned = []
    if sources:
        query = CatalogQuery.objects.create(
            run=run, round=0, strategy=PINNED_STRATEGY, payload={},
        )
        clones = [c for c in (_clone(s, run, query, run.object_snapshot) for s in sources) if c]
        Candidate.objects.bulk_create(clones)  # in order: pins sort by pk
        pinned = [c.product_gid for c in clones]
        query.payload = {'pinned': pinned}
        query.returned_count = query.new_count = query.passing_count = len(clones)
        query.save(update_fields=['payload', 'returned_count', 'new_count', 'passing_count'])
    feedback['pinned'] = pinned
    SearchRun.objects.filter(pk=run.pk).update(feedback=feedback)
    run.feedback = feedback
    return pinned


def excluded_gids(run):
    """Products a refine run must never return: already shown in the row,
    pinned, or the row's current pick. (Disliked ones are dropped for every run.)"""
    fb = run.feedback or {}
    out = set(fb.get('seen') or []) | set(fb.get('pinned') or [])
    selected = (run.object_snapshot or {}).get('selectedCandidateId')
    if selected is not None:
        out |= set(Candidate.objects.filter(pk=selected).values_list('product_gid', flat=True))
    return out
