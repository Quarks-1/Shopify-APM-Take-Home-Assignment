"""Vibe import worker: `manage.py run_vibe_import <import_id>`.

Started by api.vibes.importer.dispatch() in its own session, with stdout and
stderr going to <VIBES_DIR>/imports/<import_id>/worker.log. SIGTERM (a space
being deleted) sets a flag; the running import stops its batches and exits.
"""
import json
import signal
import sys
import uuid

from django.core.management.base import BaseCommand

from api.models import VibeImport
from api.vibes import importer


class Command(BaseCommand):
    help = 'Import one Pinterest board into its space and sort the pins, then dispatch queued imports.'

    def add_arguments(self, parser):
        parser.add_argument('import_id')

    def _emit(self, doc):
        self.stdout.write(json.dumps(doc, ensure_ascii=False))

    def handle(self, *args, **options):
        try:
            import_id = uuid.UUID(options['import_id'])
        except ValueError:
            self._emit({'error': 'import_id is not a valid UUID'})
            sys.exit(1)
        signal.signal(signal.SIGTERM, lambda signum, frame: importer.CANCEL.set())
        if not VibeImport.objects.filter(pk=import_id).exists():
            self._emit({'error': f'import not found: {import_id}'})
            importer.dispatch()
            sys.exit(1)
        importer.execute(import_id)
        imp = VibeImport.objects.filter(pk=import_id).first()
        if imp is None:  # space deleted while the import was in flight
            self._emit({'import': str(import_id), 'status': 'deleted'})
            return
        self._emit({
            'import': str(imp.id), 'status': imp.status, 'step': imp.step,
            'pinsFound': imp.pins_found, 'pinsDownloaded': imp.pins_downloaded,
            'pinsCategorized': imp.pins_categorized, 'pinsUsable': imp.pins_usable,
            'error': imp.error or None, 'errorCode': imp.error_code or None,
        })
