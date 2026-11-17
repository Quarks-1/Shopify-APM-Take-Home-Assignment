"""Pattern names and an item box on inspiration pins, with a text backfill.

The lexicon below is a frozen copy of api.vibes.patterns at the time of this
migration, so later changes to that module never alter what this one does.
"""
import re

from django.db import migrations, models

LEXICON = {
    'leopard print': 'leopard print', 'leopard spot': 'leopard print',
    'leopard spots': 'leopard print', 'leopard': 'leopard print',
    'zebra stripe': 'zebra print', 'zebra stripes': 'zebra print',
    'zebra print': 'zebra print', 'zebra': 'zebra print',
    'cheetah print': 'cheetah print', 'cheetah': 'cheetah print',
    'tiger stripe': 'tiger print', 'tiger stripes': 'tiger print',
    'tiger print': 'tiger print', 'tiger': 'tiger print',
    'cowhide': 'cowhide', 'cow hide': 'cowhide', 'cow print': 'cowhide',
    'cow spot': 'cowhide', 'cow spots': 'cowhide',
    'giraffe print': 'giraffe print', 'giraffe': 'giraffe print',
    'snakeskin': 'snakeskin', 'snake skin': 'snakeskin', 'snake print': 'snakeskin',
    'python print': 'snakeskin', 'snake pattern': 'snakeskin',
    'tortoiseshell': 'tortoiseshell', 'tortoise shell': 'tortoiseshell',
    'tortoise': 'tortoiseshell',
    'animal print': 'animal print', 'animal prints': 'animal print',
    'stripe': 'stripe', 'stripes': 'stripe', 'striped': 'stripe',
    'check': 'check', 'checks': 'check', 'checked': 'check', 'checkered': 'check',
    'checkerboard': 'check', 'gingham': 'check', 'plaid': 'check',
    'floral': 'floral',
    'polka dot': 'polka dot', 'polka dots': 'polka dot', 'polkadot': 'polka dot',
    'polkadots': 'polka dot',
}
LIMIT = 6
_RES = [
    (re.compile(r'(?<![a-z0-9])' + re.escape(p) + r'(?![a-z0-9])'), LEXICON[p])
    for p in sorted(LEXICON, key=lambda p: (-len(p.split()), -len(p)))
]


def _find(text):
    work = re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()
    hits = []
    for regex, canonical in _RES:
        hits += [(m.start(), canonical) for m in regex.finditer(work)]
        work = regex.sub(lambda m: ' ' * len(m.group()), work)
    hits.sort()
    out = []
    for _, canonical in hits:
        if canonical not in out:
            out.append(canonical)
    return out


def backfill_patterns(apps, schema_editor):
    Inspiration = apps.get_model('api', 'SpaceInspiration')
    for row in Inspiration.objects.filter(categorized_at__isnull=False).iterator():
        texts = [row.description, row.item]
        for value in (row.styles, row.materials):
            texts += list(value) if isinstance(value, list) else []
        found = []
        for text in texts:
            for pattern in _find(text):
                if pattern not in found:
                    found.append(pattern)
        found = found[:LIMIT]
        if found:
            Inspiration.objects.filter(pk=row.pk).update(patterns=found)


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0013_vibes'),
    ]

    operations = [
        migrations.AddField(
            model_name='spaceinspiration',
            name='patterns',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='spaceinspiration',
            name='model_patterns',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='spaceinspiration',
            name='item_box',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_patterns, migrations.RunPython.noop),
    ]
