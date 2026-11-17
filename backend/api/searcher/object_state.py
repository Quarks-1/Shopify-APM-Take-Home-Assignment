"""Keep SpaceObject.search truthful: it mirrors the object's latest SearchRun.

The shape is exactly the frontend's SearchState:
    {status: idle|running|done|empty, step?, lastRunAt?}

Always derived from the newest run of the object (not from whichever run
happens to call in), so a late write from an older run can never clobber
the state of a newer one.
"""
from django.db import transaction

from ..models import RunStatus, RunStep, SearchRun, SpaceObject

ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING)
TERMINAL_TO_SEARCH = {
    RunStatus.DONE: 'done',
    RunStatus.EMPTY: 'empty',
    RunStatus.FAILED: 'idle',
    RunStatus.CANCELED: 'idle',
}


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def search_state(run, previous_finished_at=None):
    if run.status in ACTIVE:
        out = {'status': 'running', 'step': run.step or RunStep.PLANNING}
        if previous_finished_at is not None:
            out['lastRunAt'] = _iso(previous_finished_at)
        return out
    out = {'status': TERMINAL_TO_SEARCH.get(run.status, 'idle')}
    when = run.finished_at or run.started_at or run.created_at
    if when is not None:
        out['lastRunAt'] = _iso(when)
    return out


def sync_object_search(object_id):
    """Rewrite SpaceObject.search from the object's newest run. Returns the
    new state, or None when the object has no runs (left untouched)."""
    with transaction.atomic():
        runs = SearchRun.objects.filter(object_id=object_id).order_by('-created_at', '-pk')
        latest = runs.only('status', 'step', 'created_at', 'started_at', 'finished_at').first()
        if latest is None:
            return None
        previous = None
        if latest.status in ACTIVE:
            previous = (
                runs.exclude(pk=latest.pk).filter(finished_at__isnull=False)
                .values_list('finished_at', flat=True).first()
            )
        state = search_state(latest, previous)
        SpaceObject.objects.filter(pk=object_id).update(search=state)
        return state
