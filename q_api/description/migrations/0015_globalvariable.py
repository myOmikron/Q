# Generated by Django 3.2.4 on 2021-07-07 17:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('description', '0014_auto_20210707_0232'),
    ]

    operations = [
        migrations.CreateModel(
            name='GlobalVariable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
        ),
    ]
