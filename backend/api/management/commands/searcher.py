"""Searcher CLI: one JSON document on stdout per call.

Everything except `start` takes the run ONLY from the SEARCH_RUN_ID
environment variable (deliberately no --run flag).
"""
import json
import os
import sys
import uuid

from django.core.management.base import BaseCommand

from api.models import AgentMode, SearchRun, SpaceObject
from api.searcher import engine


class _Fail(Exception):
    def __init__(self, message, **extra):
        super().__init__(message)
        self.message, self.extra = message, extra


def _run_from_env():
    raw = os.environ.get('SEARCH_RUN_ID', '').strip()
    if not raw:
        raise _Fail('SEARCH_RUN_ID is not set')
    try:
        run_id = uuid.UUID(raw)
    except ValueError:
        raise _Fail('SEARCH_RUN_ID is not a valid UUID')
    try:
        return SearchRun.objects.get(pk=run_id)
    except SearchRun.DoesNotExist:
        raise _Fail(f'run not found: {run_id}')


def _parse_rejects(items):
    out = {}
    for item in items or []:
        key, sep, reason = item.partition('=')
        try:
            cid = int(key.strip())
        except ValueError:
            raise _Fail(f'bad --reject value (expected ID=reason): {item}')
        out[cid] = reason.strip().strip('"\'') if sep else ''
    return out


class Command(BaseCommand):
    help = 'Product searcher loop (JSON in, JSON out).'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        p = sub.add_parser('start', help='dev helper: create a run for an object')
        p.add_argument('--object', required=True, dest='object_id')
        p = sub.add_parser('next')
        p.add_argument('--strategy')
        p = sub.add_parser('judge')
        p.add_argument('--accept', nargs='*', type=int, default=[])
        p.add_argument('--reject', nargs='*', default=[], metavar='ID=REASON')
        sub.add_parser('status')
        sub.add_parser('auto')

    def _emit(self, doc):
        self.stdout.write(json.dumps(doc, ensure_ascii=False))

    def handle(self, *args, **options):
        action = options['action']
        try:
            if action == 'start':
                try:
                    obj = SpaceObject.objects.get(pk=options['object_id'])
                except SpaceObject.DoesNotExist:
                    raise _Fail(f'object not found: {options["object_id"]}')
                run = engine.create_run(obj, agent_mode=AgentMode.MANUAL)
                doc = {'run': str(run.id)}
            else:
                run = _run_from_env()
                if action == 'next':
                    doc = engine.next_round(run, options.get('strategy'))
                elif action == 'judge':
                    doc = engine.judge(
                        run, options['accept'], _parse_rejects(options['reject'])
                    )
                elif action == 'status':
                    doc = engine.status(run)
                else:
                    doc = engine.run_deterministic(run)
        except (_Fail, engine.SearcherError) as exc:
            self._emit({'error': exc.message, **exc.extra})
            sys.exit(1)
        self._emit(doc)
