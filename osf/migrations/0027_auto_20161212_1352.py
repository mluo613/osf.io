# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2016-12-12 19:52
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0026_preprintservice_license'),
    ]

    operations = [
        migrations.AlterField(
            model_name='osfuser',
            name='locale',
            field=models.CharField(blank=True, default=b'en_US', max_length=255),
        ),
        migrations.AlterField(
            model_name='osfuser',
            name='timezone',
            field=models.CharField(blank=True, default=b'Etc/UTC', max_length=255),
        ),
    ]
