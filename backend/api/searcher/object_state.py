"""Keep SpaceObject.search truthful: it mirrors the object's latest SearchRun.

The shape is exactly the frontend's SearchState:
    {status: idle|running|done|empty, step?, lastRunAt?}

Always derived from the newest run of the object (not from whichever run
happens to call in), so a late write from an older run can never clobber
the state of a newer one.
"""
from django.db import transaction

from ..models import Candidate, RunStatus, RunStep, SearchMode, SearchRun, SpaceObject, Verdict

ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING)
TERMINAL_TO_SEARCH = {
    RunStatus.DONE: 'done',
    RunStatus.EMPTY: 'empty',
    RunStatus.FAILED: 'idle',
    RunStatus.CANCELED: 'idle',
}


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def search_state(run, previous_finished_at=None, *, has_prior_matches=False):
    if run.status in ACTIVE:
        out = {'status': 'running', 'step': run.step or RunStep.PLANNING}
        if previous_finished_at is not None:
            out['lastRunAt'] = _iso(previous_finished_at)
        return out
    status = TERMINAL_TO_SEARCH.get(run.status, 'idle')
    if (
        run.status == RunStatus.EMPTY
        and getattr(run, 'mode', None) in (SearchMode.LIKE, SearchMode.REFINE)
        and has_prior_matches
    ):
        status = 'done'
    out = {'status': status}
    when = run.finished_at or run.started_at or run.created_at
    if when is not None:
        out['lastRunAt'] = _iso(when)
    return out


def sync_object_search(object_id):
    """Rewrite SpaceObject.search from the object's newest run. Returns the
    new state, or None when the object has no runs (left untouched)."""
    with transaction.atomic():
        runs = SearchRun.objects.filter(object_id=object_id).order_by('-created_at', '-pk')
        latest = runs.only('status', 'mode', 'step', 'created_at', 'started_at', 'finished_at').first()
        if latest is None:
            return None
        previous = None
        has_prior_matches = False
        if latest.status in ACTIVE:
            previous = (
                runs.exclude(pk=latest.pk).filter(finished_at__isnull=False)
                .values_list('finished_at', flat=True).first()
            )
        elif latest.status == RunStatus.EMPTY and latest.mode in (SearchMode.LIKE, SearchMode.REFINE):
            has_selected = SpaceObject.objects.filter(
                pk=object_id, selected_candidate_id__isnull=False
            ).exists()
            has_prior_matches = has_selected or Candidate.objects.filter(
                run__object_id=object_id,
                run__status=RunStatus.DONE,
                verdict=Verdict.ACCEPTED,
            ).exclude(run_id=latest.pk).exists()
        state = search_state(latest, previous, has_prior_matches=has_prior_matches)
        SpaceObject.objects.filter(pk=object_id).update(search=state)
        return state
