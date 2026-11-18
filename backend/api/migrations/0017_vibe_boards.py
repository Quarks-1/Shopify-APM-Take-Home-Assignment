"""Promote Pinterest boards to a shared VibeBoard model (one active board per space).

Existing per-space SpaceInspiration rows and VibeImport records are grouped into
VibeBoard rows (deduplicated by canonical resolved_url / board_url), linked via
Space.vibe_board, and their stored JPEG / crop files are moved into
<vibes_dir>/boards/<board_id>/.
"""
import logging
import shutil
import uuid

import django.db.models.deletion
from django.db import migrations, models

from api.vibes import paths

log = logging.getLogger(__name__)


def _move_pin_files(old_rel, board_id, pin_id):
    """Move a stored pin image (and its crop sibling) into boards/<board_id>/.
    Returns the new relative image_path, or `old_rel` when there is no local file."""
    if not old_rel:
        return ''
    src = paths.inside_vibes_dir(old_rel)
    if src is None or not src.is_file():
        return old_rel
    new_rel = f'boards/{board_id}/{pin_id}.jpg'
    dst = paths.inside_vibes_dir(new_rel)
    if dst is None:
        return old_rel
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src != dst:
            shutil.move(str(src), str(dst))
        crop_src = src.with_name(f'{src.stem}{paths.CROP_SUFFIX}')
        crop_dst = dst.with_name(f'{dst.stem}{paths.CROP_SUFFIX}')
        if crop_src.is_file() and crop_src != crop_dst:
            shutil.move(str(crop_src), str(crop_dst))
        return new_rel
    except OSError:
        return old_rel


def _remove_file_pair(rel):
    if not rel:
        return
    target = paths.inside_vibes_dir(rel)
    if target is None:
        return
    for victim in (target, target.with_name(f'{target.stem}{paths.CROP_SUFFIX}')):
        try:
            victim.unlink()
        except OSError:
            pass


def migrate_to_vibe_boards(apps, schema_editor):
    Space = apps.get_model('api', 'Space')
    VibeBoard = apps.get_model('api', 'VibeBoard')
    VibeImport = apps.get_model('api', 'VibeImport')
    Inspiration = apps.get_model('api', 'SpaceInspiration')

    boards_by_key = {}

    def get_or_make_board(key, *, board_url, resolved_url='', title=''):
        board = boards_by_key.get(key)
        if board is None:
            board = VibeBoard.objects.create(
                board_url=board_url,
                resolved_url=resolved_url,
                title=title[:200],
            )
            boards_by_key[key] = board
            if resolved_url:
                boards_by_key.setdefault(resolved_url, board)
            if board_url:
                boards_by_key.setdefault(board_url, board)
        else:
            updates = {}
            if resolved_url and not board.resolved_url:
                updates['resolved_url'] = resolved_url
            if title and not board.title:
                updates['title'] = title[:200]
            if updates:
                VibeBoard.objects.filter(pk=board.pk).update(**updates)
                for k, v in updates.items():
                    setattr(board, k, v)
        return board

    for space in Space.objects.all().order_by('position', 'id'):
        pins = list(Inspiration.objects.filter(space_id=space.id).order_by('board_rank', 'pk'))
        imports = list(VibeImport.objects.filter(space_id=space.id).order_by('-created_at', '-pk'))
        if not pins:
            # Link historical done imports if a matching board already exists.
            for imp in imports:
                k = (imp.resolved_url or imp.board_url or '').strip()
                if k and k in boards_by_key:
                    VibeImport.objects.filter(pk=imp.pk).update(board_id=boards_by_key[k].pk)
            continue

        # Prefer the newest import referenced by this space's pins, else newest done/any import.
        pin_import_ids = {p.vibe_import_id for p in pins if p.vibe_import_id is not None}
        current_imp = (
            next((i for i in imports if i.pk in pin_import_ids), None)
            or next((i for i in imports if i.status == 'done'), None)
            or (imports[0] if imports else None)
        )
        if current_imp is not None:
            board_url = current_imp.board_url
            resolved_url = current_imp.resolved_url or ''
            title = current_imp.board_title or ''
            key = (resolved_url or board_url).strip() or f'space:{space.id}'
        else:
            board_url = f'https://www.pinterest.com/imported/{space.id}/'
            resolved_url = ''
            title = space.name
            key = f'space:{space.id}'

        board = get_or_make_board(key, board_url=board_url, resolved_url=resolved_url, title=title)
        Space.objects.filter(pk=space.pk).update(vibe_board_id=board.pk)
        if current_imp is not None:
            VibeImport.objects.filter(pk=current_imp.pk).update(board_id=board.pk)
        for imp in imports:
            imp_key = (imp.resolved_url or imp.board_url or '').strip()
            if imp_key == key:
                VibeImport.objects.filter(pk=imp.pk).update(board_id=board.pk)

        existing_by_pin = {
            r.pin_id: r for r in Inspiration.objects.filter(board_id=board.pk)
        }
        for row in pins:
            already = existing_by_pin.get(row.pin_id)
            if already is not None:
                old_rel = row.image_path
                updates = {}
                if already.categorized_at is None and row.categorized_at is not None:
                    for field in (
                        'categories', 'item', 'styles', 'colors', 'materials',
                        'description', 'patterns', 'model_patterns', 'item_box', 'categorized_at',
                    ):
                        updates[field] = getattr(row, field)
                if not already.image_path and old_rel:
                    updates['image_path'] = _move_pin_files(old_rel, board.pk, row.pin_id)
                    old_rel = ''
                if updates:
                    Inspiration.objects.filter(pk=already.pk).update(**updates)
                Inspiration.objects.filter(pk=row.pk).delete()
                if old_rel and old_rel != already.image_path:
                    _remove_file_pair(old_rel)
                continue

            new_rel = _move_pin_files(row.image_path, board.pk, row.pin_id)
            Inspiration.objects.filter(pk=row.pk).update(board_id=board.pk, image_path=new_rel)
            row.board_id = board.pk
            row.image_path = new_rel
            existing_by_pin[row.pin_id] = row

        try:
            legacy_dir = paths.space_dir(space.id)
            if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
                legacy_dir.rmdir()
        except (OSError, ValueError):
            pass

    Inspiration.objects.filter(board_id__isnull=True).delete()

    for board in VibeBoard.objects.all():
        usable = sum(
            1
            for cats in Inspiration.objects.filter(
                board_id=board.pk, categorized_at__isnull=False
            ).values_list('categories', flat=True)
            if cats
        )
        if board.pins_usable != usable:
            VibeBoard.objects.filter(pk=board.pk).update(pins_usable=usable)


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_repair_vibe_counts_and_crops'),
    ]

    operations = [
        migrations.CreateModel(
            name='VibeBoard',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('board_url', models.TextField()),
                ('resolved_url', models.TextField(blank=True, db_index=True, default='')),
                ('title', models.CharField(blank=True, default='', max_length=200)),
                ('pins_usable', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.AddField(
            model_name='space',
            name='vibe_board',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='spaces',
                to='api.vibeboard',
            ),
        ),
        migrations.AddField(
            model_name='vibeimport',
            name='board',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='imports',
                to='api.vibeboard',
            ),
        ),
        migrations.AlterField(
            model_name='vibeimport',
            name='space',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vibe_imports',
                to='api.space',
            ),
        ),
        migrations.AddField(
            model_name='spaceinspiration',
            name='board',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='inspirations',
                to='api.vibeboard',
            ),
        ),
        migrations.RunPython(migrate_to_vibe_boards, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='spaceinspiration',
            name='unique_space_pin',
        ),
        migrations.RemoveField(
            model_name='spaceinspiration',
            name='space',
        ),
        migrations.AlterField(
            model_name='spaceinspiration',
            name='board',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='inspirations',
                to='api.vibeboard',
            ),
        ),
        migrations.AddConstraint(
            model_name='spaceinspiration',
            constraint=models.UniqueConstraint(fields=('board', 'pin_id'), name='unique_board_pin'),
        ),
    ]
