# Generated by Django 3.2.6 on 2021-08-27 19:26

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('proxy', '0002_auto_20210824_1356'),
    ]

    operations = [
        migrations.AddField(
            model_name='checkresult',
            name='context',
            field=models.CharField(default='', max_length=255),
        ),
        migrations.AddField(
            model_name='checkresult',
            name='object_id',
            field=models.PositiveIntegerField(default=0),
        ),
    ]