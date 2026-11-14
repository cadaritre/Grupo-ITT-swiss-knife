"""Cliente de cada reporte: columna con índice para buscar y filtrar, copia de `datos["cliente"]`.

Solo agrega la columna (vacía por omisión) y la llena desde los datos de los reportes que ya lo traen. Revertir quita la
columna; los datos del reporte no se tocan.
"""
from django.db import migrations, models


def llenar(apps, schema_editor):
    Reporte = apps.get_model("herramientas", "Reporte")
    for r in Reporte.objects.filter(cliente="").only("pk", "datos").iterator():
        cliente = " ".join(str((r.datos or {}).get("cliente") or "").split())[:160]
        if cliente:
            Reporte.objects.filter(pk=r.pk).update(cliente=cliente)


class Migration(migrations.Migration):

    dependencies = [
        ('herramientas', '0010_autoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='reporte',
            name='cliente',
            field=models.CharField(blank=True, db_index=True, max_length=160),
        ),
        migrations.RunPython(llenar, migrations.RunPython.noop),
    ]
