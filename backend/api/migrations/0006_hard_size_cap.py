"""Re-evaluate saved candidates after the hard size cap change."""

from django.conf import settings
from django.db import migrations


def _candidate_fields(cand):
    return {
        'price_cents': cand.price_cents,
        'currency': cand.currency,
        'dims': cand.dims,
        'dims_longest': cand.dims_longest,
        'weight_lb': cand.weight_lb,
        'rating_value': cand.rating_value,
    }


def reevaluate_candidates(apps, schema_editor):
    from api.searcher import filters

    Candidate = apps.get_model('api', 'Candidate')
    SpaceObject = apps.get_model('api', 'SpaceObject')
    tolerance = settings.SEARCHER_SIZE_TOLERANCE

    for cand in Candidate.objects.select_related('run').iterator():
        snap = cand.run.object_snapshot or {}
        passes, reasons, size_status, weight_status = filters.evaluate(
            _candidate_fields(cand), snap, tolerance,
        )
        updates = []
        if cand.passes_filters != passes:
            cand.passes_filters = passes
            updates.append('passes_filters')
        if cand.size_status != size_status:
            cand.size_status = size_status
            updates.append('size_status')
        if cand.weight_status != weight_status:
            cand.weight_status = weight_status
            updates.append('weight_status')
        if list(cand.filter_reasons or []) != reasons:
            cand.filter_reasons = reasons
            updates.append('filter_reasons')
        if cand.verdict == 'accepted' and not passes:
            cand.verdict = 'rejected'
            if not cand.verdict_reason:
                cand.verdict_reason = 'exceeds stated size'
            updates.extend(('verdict', 'verdict_reason'))
        if updates:
            cand.save(update_fields=updates)

    for obj in SpaceObject.objects.exclude(selected_candidate_id=None).iterator():
        cand = Candidate.objects.filter(pk=obj.selected_candidate_id).first()
        if cand is None or not cand.passes_filters:
            obj.selected_candidate_id = None
            obj.save(update_fields=['selected_candidate_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0005_object_price_min'),
    ]

    operations = [
        migrations.RunPython(reevaluate_candidates, migrations.RunPython.noop),
    ]