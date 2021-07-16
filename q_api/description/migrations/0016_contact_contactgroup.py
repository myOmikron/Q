# Generated by Django 3.2.4 on 2021-07-09 02:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('description', '0015_globalvariable'),
    ]

    operations = [
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='', max_length=255, unique=True)),
                ('mail', models.EmailField(blank=True, default='', max_length=255, null=True)),
                ('linked_host_notifications', models.ManyToManyField(blank=True, related_name='contact_host_check', to='description.Check')),
                ('linked_metric_notifications', models.ManyToManyField(blank=True, related_name='contact_metric_check', to='description.Check')),
            ],
        ),
        migrations.CreateModel(
            name='ContactGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='', max_length=255, unique=True)),
                ('linked_contacts', models.ManyToManyField(blank=True, to='description.Contact')),
            ],
        ),
    ]
