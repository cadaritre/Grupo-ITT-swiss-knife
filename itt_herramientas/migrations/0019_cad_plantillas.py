"""«Mis plantillas» de CAD (18-11-2026): la marca por cuenta de los planos de la biblioteca que se usan como plantilla de presentación."""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("herramientas", "0018_formatos_puntos_civil"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlantillaCad",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cuenta_id", models.BigIntegerField(db_index=True)),
                ("creada", models.DateTimeField(default=django.utils.timezone.now)),
                ("usada", models.DateTimeField(blank=True, null=True)),
                ("plano", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="plantillas", to="herramientas.plano")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("cuenta_id", "plano"), name="herramientas_plantillacad_unica")],
            },
        ),
    ]
