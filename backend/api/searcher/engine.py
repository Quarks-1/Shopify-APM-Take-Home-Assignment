"""The searcher loop: one deterministic step at a time.

`next_round` queries the catalog and stores filtered candidates; `judge`
records accept/reject verdicts; `run_deterministic` is the no-LLM fallback.
The accepted count is always computed from Candidate rows.
"""
import time
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import (
    Candidate,
    CatalogQuery,
    RunStatus,
    RunStep,
    SearchMode,
    SearchRun,
    Verdict,
)
from ..serializers import object_to_json, seller_group_key
from . import filters, query_builder, ucp_client
from .normalize import normalize_product
from .object_state import sync_object_search

REASON_LEN = 200
MAX_COMPACT_SPECS = 6
SUMMARY_LEN = 200


class SearcherError(Exception):
    def __init__(self, message, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


# ------------------------------------------------------------------ helpers

def _accepted(run):
    return run.candidates.filter(verdict=Verdict.ACCEPTED).count()


def _pending(run):
    return run.candidates.filter(passes_filters=True, verdict=Verdict.PENDING).count()


def _previous_queries(run):
    return [
        {
            'round': q.round, 'strategy': q.strategy,
            'query': (q.payload or {}).get('query', ''),
            'payload': q.payload, 'response_meta': q.response_meta,
            'passing_count': q.passing_count,
        }
        for q in run.queries.all()
    ]


def available_strategies(run):
    prev = _previous_queries(run)
    used = [q['query'] for q in prev]
    return query_builder.strategies(run.object_snapshot, used, prev, like=_like(run))


def _like(run):
    """The run's reference product and photos, or None for a text search."""
    return (run.like_source or {}) if run.mode == SearchMode.LIKE else None


def _default_strategy(run, available):
    """After expand threshold, prefer broadening before more ladder steps."""
    if _accepted(run) >= run.target or run.mode == SearchMode.LIKE:
        return available[0]  # like runs walk their photo ladder in order
    if run.round_count >= settings.SEARCHER_EXPAND_AFTER:
        for name in ('broad', 'more'):
            if name in available:
                return name
    return available[0]


_STEP_ORDER = {
    RunStep.PLANNING: 0, RunStep.BUILDING_QUERY: 1,
    RunStep.QUERYING_CATALOG: 2, RunStep.RANKING: 3,
}


def _set_step(run, step):
    """Advance the run's step; never backwards, never on a non-running run."""
    with transaction.atomic():
        rows = list(SearchRun.objects.filter(pk=run.pk, status=RunStatus.RUNNING)
                    .values_list('step', flat=True))
        if not rows:
            return False
        current = rows[0]
        if current == step or _STEP_ORDER.get(current, -1) >= _STEP_ORDER[step]:
            run.step = current
            return False
        changed = SearchRun.objects.filter(pk=run.pk, status=RunStatus.RUNNING).update(step=step)
        if changed:
            run.step = step
            sync_object_search(run.object_id)
        return bool(changed)


def _wrapping_up(run, available):
    return run.max_rounds - run.round_count <= 0 or not available


def _price(cents):
    return f'${cents / 100:,.2f}'


def _num(v):
    return f'{round(float(v), 2):g}'


def _size_text(c):
    dims = c.dims or {}
    if dims.get('x') is not None and dims.get('y') is not None:
        txt = ' x '.join(_num(dims[k]) for k in ('x', 'y', 'z') if dims.get(k) is not None) + ' in'
        return f'{txt} ({c.size_status})'
    if dims:
        letters = {'x': 'W', 'y': 'D', 'z': 'H'}
        txt = ', '.join(f'{_num(dims[k])} in {letters[k]}' for k in ('x', 'y', 'z') if k in dims)
        return f'{txt} ({c.size_status})'
    if c.dims_longest is not None:
        return f'~{_num(c.dims_longest)} in longest ({c.size_status})'
    return c.size_status


def _compact(c):
    return {
        'id': c.pk,
        'title': c.title,
        'seller': c.seller_name,
        'price': _price(c.price_cents),
        'size': _size_text(c),
        'weight': f'{_num(c.weight_lb)} lb' if c.weight_lb is not None else None,
        'materials': c.material_text or ', '.join(c.materials) or None,
        'specs': list(c.specs)[:MAX_COMPACT_SPECS],
        'summary': (c.description or '')[:SUMMARY_LEN],
    }


def _stop(reason, run):
    return {
        'stop': True, 'reason': reason, 'status': run.status,
        'accepted': _accepted(run), 'target': run.target,
    }


# ------------------------------------------------------------------- runs

def create_run(space_object, status=RunStatus.RUNNING, agent_mode='',
               mode=SearchMode.SEARCH, like_source=None):
    """A running run (CLI `start`, tests) or a queued one for the runner."""
    snapshot = object_to_json(space_object)
    placement = space_object.placements.select_related('space').order_by('pk').first()
    if placement is not None:
        snapshot['space'] = {'type': placement.space.type, 'dims': placement.space.dims}
    running = status == RunStatus.RUNNING
    run = SearchRun.objects.create(
        object=space_object,
        object_snapshot=snapshot,
        status=status,
        step=RunStep.PLANNING if running else None,
        target=settings.SEARCHER_TARGET,
        max_rounds=settings.SEARCHER_MAX_ROUNDS,
        started_at=timezone.now() if running else None,
        agent_mode=agent_mode,
        mode=mode,
        like_source=like_source,
    )
    sync_object_search(space_object.pk)
    return run


def next_round(run, strategy=None):
    run.refresh_from_db()
    if run.status != RunStatus.RUNNING:
        return _stop(f'run is not running (status: {run.status})', run)
    accepted = _accepted(run)
    if accepted >= run.target:
        _set_step(run, RunStep.RANKING)
        return _stop('target reached', run)
    if run.round_count >= run.max_rounds:
        _set_step(run, RunStep.RANKING)
        return _stop('max rounds reached', run)
    available = available_strategies(run)
    if not available:
        _set_step(run, RunStep.RANKING)
        return _stop('no strategies left', run)
    if strategy is None:
        strategy = _default_strategy(run, available)
    elif strategy not in available:
        raise SearcherError(
            f'unknown or unavailable strategy: {strategy}', available=available
        )

    snapshot = run.object_snapshot
    _set_step(run, RunStep.BUILDING_QUERY)
    like = _like(run)
    previous = _previous_queries(run)
    payload = stored = None
    error = ''
    try:
        stored = query_builder.build_stored_payload(snapshot, strategy, previous, like)
        payload = query_builder.build_payload(snapshot, strategy, previous, like)
    except (ValueError, OSError) as exc:
        if like is None:
            raise
        error = f'could not build like query: {exc}'  # e.g. a cached photo vanished
        stored = {'like_error': True}
    _set_step(run, RunStep.QUERYING_CATALOG)

    run.round_count += 1
    run.save(update_fields=['round_count'])
    started = time.monotonic()
    envelope = None
    if payload is not None:
        try:
            envelope = ucp_client.search_catalog(payload)
        except ucp_client.UcpError as exc:
            error = str(exc)
    duration_ms = int((time.monotonic() - started) * 1000)

    if envelope is None:
        CatalogQuery.objects.create(
            run=run, round=run.round_count, strategy=strategy, payload=stored,
            response_meta={}, error=error, duration_ms=duration_ms,
        )
        left = available_strategies(run)
        if _wrapping_up(run, left):
            _set_step(run, RunStep.RANKING)
        return {
            'error': error, 'round': run.round_count, 'strategy': strategy,
            'query': stored.get('query', ''), 'accepted': accepted, 'target': run.target,
            'rounds_left': run.max_rounds - run.round_count,
            'strategies_left': left,
        }

    result = envelope['result']
    products = [p for p in (result.get('products') or []) if isinstance(p, dict)]
    pagination = result.get('pagination') or {}
    meta = {
        'total_count': pagination.get('total_count'),
        'has_next_page': bool(pagination.get('has_next_page')),
        'cursor': pagination.get('cursor'),
        'messages': (result.get('messages') or [])[:10],
    }

    known = set(run.candidates.values_list('product_gid', flat=True))
    if like and like.get('productGid'):
        known.add(like['productGid'])  # the reference product is never its own match
    rows = []
    with transaction.atomic():
        cq = CatalogQuery.objects.create(
            run=run, round=run.round_count, strategy=strategy, payload=stored,
            response_meta=meta, returned_count=len(products), duration_ms=duration_ms,
        )
        for product in products:
            fields = normalize_product(product)
            gid = fields['product_gid']
            if not gid or not fields['title'] or gid in known:
                continue
            known.add(gid)
            passes, reasons, size_status, weight_status = filters.evaluate(
                fields, snapshot, settings.SEARCHER_SIZE_TOLERANCE
            )
            rows.append(Candidate(
                run=run, query=cq, size_status=size_status, weight_status=weight_status,
                passes_filters=passes, filter_reasons=reasons, **fields,
            ))
        Candidate.objects.bulk_create(rows)
        passing = sum(1 for r in rows if r.passes_filters)
        cq.new_count = len(rows)
        cq.passing_count = passing
        cq.save(update_fields=['new_count', 'passing_count'])
    left = available_strategies(run)
    if _wrapping_up(run, left):
        _set_step(run, RunStep.RANKING)

    new_passing = list(
        run.candidates.filter(query=cq, passes_filters=True).order_by('pk')
    )
    return {
        'round': run.round_count,
        'strategy': strategy,
        'query': stored.get('query', ''),
        'returned': len(products),
        'new': len(rows),
        'passing': passing,
        'filtered_out': len(rows) - passing,
        'accepted': accepted,
        'target': run.target,
        'rounds_left': run.max_rounds - run.round_count,
        'strategies_left': left,
        'candidates': [_compact(c) for c in new_passing],
    }


def judge(run, accept_ids=(), rejects=None):
    accept_ids = [int(i) for i in accept_ids or []]
    rejects = {int(k): v for k, v in (rejects or {}).items()}
    both = set(accept_ids) & set(rejects)
    if both:
        raise SearcherError(f'ids both accepted and rejected: {sorted(both)}')
    wanted = set(accept_ids) | set(rejects)
    with transaction.atomic():
        found = {c.pk: c for c in Candidate.objects.filter(run=run, pk__in=wanted)}
        missing = sorted(wanted - set(found))
        if missing:
            raise SearcherError(f'candidate ids not in this run: {missing}')
        failing = sorted(i for i in accept_ids if not found[i].passes_filters)
        if failing:
            raise SearcherError(
                f'cannot accept candidates that failed hard filters: {failing}'
            )
        for i in set(accept_ids):
            c = found[i]
            c.verdict, c.verdict_reason = Verdict.ACCEPTED, ''
            c.save(update_fields=['verdict', 'verdict_reason'])
        for i, reason in rejects.items():
            c = found[i]
            c.verdict = Verdict.REJECTED
            c.verdict_reason = str(reason or '').strip()[:REASON_LEN]
            c.save(update_fields=['verdict', 'verdict_reason'])
    accepted = _accepted(run)
    if (accepted >= run.target or run.max_rounds - run.round_count <= 0
            or not available_strategies(run)):
        _set_step(run, RunStep.RANKING)
    sync_object_search(run.object_id)
    return {
        'accepted': accepted, 'target': run.target,
        'done': accepted >= run.target, 'pending': _pending(run),
    }


def status(run):
    run.refresh_from_db()
    snap = run.object_snapshot
    d = snap.get('dims') or {}
    return {
        'run': str(run.id),
        'status': run.status,
        'step': run.step,
        'round': run.round_count,
        'max_rounds': run.max_rounds,
        'accepted': _accepted(run),
        'target': run.target,
        'pending': _pending(run),
        'strategies_left': available_strategies(run) if run.status == RunStatus.RUNNING else [],
        'object': {
            'name': snap.get('name'), 'type': snap.get('type'),
            'style': snap.get('style'), 'material': snap.get('material'),
            'dims': d, 'priceMin': snap.get('priceMin'),
            'priceLimit': snap.get('priceLimit'),
            'minRating': snap.get('minRating'),
        },
    }


def _rank_key(c):
    return (0 if c.size_status == 'fits' else 1, c.price_cents, c.pk)


def _accepted_seller_keys(run):
    return {
        seller_group_key(c)
        for c in run.candidates.filter(verdict=Verdict.ACCEPTED).only(
            'seller_domain', 'seller_name', 'pk'
        )
    }


def _pick_deterministic_accepts(run, candidate_ids, need):
    """Prefer one new seller per round, then fill by price among verified fits."""
    if need <= 0 or not candidate_ids:
        return []
    pending = list(
        Candidate.objects.filter(pk__in=candidate_ids, passes_filters=True)
    )
    if not pending:
        return []
    accepted_sellers = _accepted_seller_keys(run)
    by_seller = defaultdict(list)
    for c in pending:
        by_seller[seller_group_key(c)].append(c)
    for group in by_seller.values():
        group.sort(key=_rank_key)

    picked, picked_ids = [], set()
    newcomers = sorted(
        ((sk, group[0]) for sk, group in by_seller.items() if sk not in accepted_sellers),
        key=lambda t: _rank_key(t[1]),
    )
    for _sk, cand in newcomers:
        if len(picked) >= need:
            break
        picked.append(cand)
        picked_ids.add(cand.pk)

    rest = sorted((c for c in pending if c.pk not in picked_ids), key=_rank_key)
    for cand in rest:
        if len(picked) >= need:
            break
        picked.append(cand)
    return picked


def run_deterministic(run):
    """No-LLM fallback: accept the best new passing candidates each round."""
    rounds, errors = [], 0
    while True:
        out = next_round(run)
        if out.get('stop'):
            stop_reason = out['reason']
            break
        if out.get('error'):
            errors += 1
            rounds.append({'round': out['round'], 'strategy': out['strategy'],
                           'query': out['query'], 'error': out['error']})
            continue
        rounds.append({k: out[k] for k in ('round', 'strategy', 'query', 'returned',
                                           'new', 'passing', 'filtered_out')})
        need = run.target - _accepted(run)
        ids = [c['id'] for c in out['candidates']]
        best = _pick_deterministic_accepts(run, ids, need)
        if best:
            judge(run, [c.pk for c in best])

    run.refresh_from_db()
    accepted_qs = run.candidates.filter(verdict=Verdict.ACCEPTED)
    accepted = accepted_qs.count()
    finished = timezone.now()
    if run.status == RunStatus.RUNNING:  # leave canceled/failed runs alone
        if accepted == 0 and rounds and errors == len(rounds):
            status, error = RunStatus.FAILED, 'every catalog query failed'
        else:
            status, error = (RunStatus.DONE if accepted else RunStatus.EMPTY), run.error
        # Conditional, so a concurrent cancel is never overwritten.
        SearchRun.objects.filter(pk=run.pk, status=RunStatus.RUNNING).update(
            status=status, error=error, step=None, finished_at=finished)
    else:
        SearchRun.objects.filter(pk=run.pk, finished_at__isnull=True).update(finished_at=finished)
        SearchRun.objects.filter(pk=run.pk).update(step=None)
    run.refresh_from_db()
    sync_object_search(run.object_id)
    return {
        'run': str(run.id),
        'status': str(run.status),
        'accepted': accepted,
        'target': run.target,
        'rounds_used': run.round_count,
        'fits': accepted_qs.filter(size_status='fits').count(),
        'unverified': accepted_qs.filter(size_status='unverified').count(),
        'stopped': stop_reason,
        'rounds': rounds,
    }
