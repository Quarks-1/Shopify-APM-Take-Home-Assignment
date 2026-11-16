"""Search worker: `manage.py run_search <run_id>`.

Started by api.searcher.runner.dispatch() in its own session, with stdout
and stderr going to <SEARCHER_VAR_DIR>/searches/<run_id>.log. Prints one
JSON summary line when the run ends.
"""
import json
import sys
import uuid

from django.core.management.base import BaseCommand

from api.models import SearchRun, Verdict
from api.searcher import runner


class Command(BaseCommand):
    help = 'Execute one search run (agent or deterministic), then dispatch queued runs.'

    def add_arguments(self, parser):
        parser.add_argument('run_id')

    def _emit(self, doc):
        self.stdout.write(json.dumps(doc, ensure_ascii=False))

    def handle(self, *args, **options):
        try:
            run_id = uuid.UUID(options['run_id'])
        except ValueError:
            self._emit({'error': 'run_id is not a valid UUID'})
            sys.exit(1)
        run = SearchRun.objects.filter(pk=run_id).first()
        if run is None:
            self._emit({'error': f'run not found: {run_id}'})
            runner.dispatch()
            sys.exit(1)
        runner.execute(run)
        run = SearchRun.objects.filter(pk=run_id).first()
        if run is None:  # object deleted while the run was in flight
            self._emit({'run': str(run_id), 'status': 'deleted'})
            return
        self._emit({
            'run': str(run.id),
            'status': run.status,
            'accepted': run.candidates.filter(verdict=Verdict.ACCEPTED).count(),
            'target': run.target,
            'rounds': run.round_count,
            'mode': run.agent_mode,
            'fallbackReason': run.fallback_reason or None,
            'error': run.error or None,
        })
