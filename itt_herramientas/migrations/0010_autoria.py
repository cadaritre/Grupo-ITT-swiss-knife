"""Autoría de cotizaciones, reportes y croquis: nombre y correo de la cuenta que creó cada uno.

Solo agrega dos columnas con valor vacío por omisión y las llena para los documentos que ya existen, con la cuenta de su
`cuenta_id` tal como es hoy. Un documento cuya cuenta ya no existe queda vacío («Cuenta eliminada» en pantalla). Revertir
quita las columnas; nada más se toca.
"""
from django.conf import settings
from django.db import migrations, models

MODELOS = ("Cotizacion", "Reporte", "Croquis")


def _nombre_y_correo(u) -> tuple[str, str]:
    nombre = f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username
    correo = (u.email or "").strip().lower()
    if not correo and "@" in u.username:
        correo = u.username.strip().lower()
    return nombre[:150], correo[:254]


def llenar(apps, schema_editor):
    app, modelo = settings.AUTH_USER_MODEL.split(".")
    Usuario = apps.get_model(app, modelo)
    for nombre in MODELOS:
        Documento = apps.get_model("herramientas", nombre)
        cuentas = set(Documento.objects.filter(autor_nombre="", autor_correo="").values_list("cuenta_id", flat=True))
        for u in Usuario.objects.filter(pk__in=cuentas):
            autor, correo = _nombre_y_correo(u)
            Documento.objects.filter(cuenta_id=u.pk, autor_nombre="", autor_correo="").update(autor_nombre=autor, autor_correo=correo)


class Migration(migrations.Migration):

    dependencies = [
        ('herramientas', '0009_drive'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion',
            name='autor_correo',
            field=models.CharField(blank=True, default='', editable=False, max_length=254),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='autor_nombre',
            field=models.CharField(blank=True, default='', editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='croquis',
            name='autor_correo',
            field=models.CharField(blank=True, default='', editable=False, max_length=254),
        ),
        migrations.AddField(
            model_name='croquis',
            name='autor_nombre',
            field=models.CharField(blank=True, default='', editable=False, max_length=150),
        ),
        migrations.AddField(
            model_name='reporte',
            name='autor_correo',
            field=models.CharField(blank=True, default='', editable=False, max_length=254),
        ),
        migrations.AddField(
            model_name='reporte',
            name='autor_nombre',
            field=models.CharField(blank=True, default='', editable=False, max_length=150),
        ),
        migrations.RunPython(llenar, migrations.RunPython.noop),
    ]
