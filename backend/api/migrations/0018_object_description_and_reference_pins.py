from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0017_vibe_boards'),
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
