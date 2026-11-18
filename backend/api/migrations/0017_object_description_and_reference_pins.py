from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_repair_vibe_counts_and_crops'),
    ]

    operations = [
        migrations.AddField(
            model_name='spaceobject',
            name='description',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='spaceobject',
            name='reference_pins',
            field=models.JSONField(default=list),
        ),
    ]
