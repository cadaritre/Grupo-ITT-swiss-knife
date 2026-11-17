"""Documentos compartidos con el equipo (decisión del propietario, 16-11-2026).

Las cotizaciones, los reportes y los croquis dejan de ser de cada cuenta y pasan a ser de la empresa. Esta migración:

1. Agrega «modificado por», el bloqueo de edición, la papelera y la marca de copia en conflicto; el historial de versiones, los borradores
   de recuperación, los avisos y el contador de folios.
2. Folios: ninguno cambia. Donde ya había repetidos (antes se contaba por cuenta), el más antiguo se queda con el folio y los demás quedan
   marcados (`folio_repetido`) para que el propietario les asigne uno nuevo desde Supervisión. El contador de cada año sigue desde el
   mayor. Desde aquí la base de datos no admite dos cotizaciones con el mismo folio.
3. Machotes: pasan a ser de la empresa. Si dos cuentas tenían uno con el mismo nombre, el más antiguo lo conserva y los demás se renombran
   «<nombre> (de <autor>)»; un identificador repetido se cambia por uno nuevo.
4. «Modificado por» empieza siendo el autor, y la revisión actual de cada documento queda como la primera de su historial.
"""

import django.db.models.functions.text
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


def compartir(apps, schema_editor):
    import re

    from django.utils import timezone

    Cotizacion = apps.get_model("herramientas", "Cotizacion")
    Reporte = apps.get_model("herramientas", "Reporte")
    Croquis = apps.get_model("herramientas", "Croquis")
    Machote = apps.get_model("herramientas", "Machote")
    ContadorFolio = apps.get_model("herramientas", "ContadorFolio")
    VersionDocumento = apps.get_model("herramientas", "VersionDocumento")
    Usuario = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    def nombre_de(cuenta_id, respaldo=""):
        if respaldo:
            return respaldo
        u = Usuario.objects.filter(pk=cuenta_id).first()
        if u is None:
            return ""
        completo = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return (completo or getattr(u, "username", "") or "")[:150]

    # 1. Folios repetidos: el más antiguo se queda con el suyo; los demás, marcados para el propietario.
    vistos = set()
    for c in Cotizacion.objects.exclude(folio="").order_by("creada", "id"):
        if c.folio in vistos:
            Cotizacion.objects.filter(pk=c.pk).update(folio_repetido=True)
        vistos.add(c.folio)
    patron = re.compile(r"^COT-(\d{4})-(\d{1,6})$")
    mayores = {}
    for folio in vistos:
        m = patron.match(folio)
        if m:
            anio, n = int(m.group(1)), int(m.group(2))
            mayores[anio] = max(mayores.get(anio, 0), n)
    for anio, n in mayores.items():
        ContadorFolio.objects.update_or_create(anio=anio, defaults={"ultimo": n})

    # 2. Machotes de la empresa: nombres e identificadores únicos.
    nombres, ids = set(), set()
    for m in Machote.objects.order_by("creado", "id"):
        cambios = {}
        if not m.autor_nombre:
            cambios["autor_nombre"] = nombre_de(m.cuenta_id)
        nombre = m.nombre
        if nombre.casefold() in nombres:
            autor = cambios.get("autor_nombre") or m.autor_nombre or "otra cuenta"
            base, n = f"{m.nombre} (de {autor})"[:150], 2
            nombre = base
            while nombre.casefold() in nombres:
                nombre, n = f"{base} {n}", n + 1
            cambios["nombre"] = nombre[:160]
        nombres.add(nombre.casefold())
        if m.plantilla_id in ids:
            cambios["plantilla_id"] = uuid.uuid4()
        ids.add(cambios.get("plantilla_id", m.plantilla_id))
        cambios["modificado_por_id"] = m.cuenta_id
        cambios["modificado_por_nombre"] = cambios.get("autor_nombre") or m.autor_nombre
        Machote.objects.filter(pk=m.pk).update(**cambios)

    # 3. «Modificado por» = autor, y la revisión actual como primera versión del historial.
    ahora = timezone.now()
    for tipo, modelo in (("cotizacion", Cotizacion), ("reporte", Reporte), ("croquis", Croquis)):
        for d in modelo.objects.all().iterator():
            nombre = nombre_de(d.cuenta_id, d.autor_nombre)
            modelo.objects.filter(pk=d.pk).update(modificado_por_id=d.cuenta_id, modificado_por_nombre=nombre)
            VersionDocumento.objects.create(tipo=tipo, documento_id=d.pk, revision=d.revision, datos=d.datos, perfil=d.perfil, cuenta_id=d.cuenta_id,
                                            autor_nombre=nombre, nota="Versión al compartir con el equipo", inicio=d.actualizada or ahora,
                                            creada=d.actualizada or ahora)


class Migration(migrations.Migration):

    dependencies = [
        ('herramientas', '0015_borrador_sesion'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AvisoDocumento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cuenta_id', models.BigIntegerField(db_index=True)),
                ('tipo', models.CharField(choices=[('cotizacion', 'Cotización'), ('reporte', 'Reporte fotográfico'), ('croquis', 'Croquis de ubicación')], max_length=10)),
                ('documento_id', models.UUIDField()),
                ('texto', models.CharField(max_length=400)),
                ('creado', models.DateTimeField(default=django.utils.timezone.now)),
                ('visto', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-creado'],
            },
        ),
        migrations.CreateModel(
            name='BorradorRecuperacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('cotizacion', 'Cotización'), ('reporte', 'Reporte fotográfico'), ('croquis', 'Croquis de ubicación')], max_length=10)),
                ('documento_id', models.UUIDField()),
                ('cuenta_id', models.BigIntegerField(db_index=True)),
                ('datos', models.JSONField()),
                ('revision_base', models.PositiveIntegerField()),
                ('quien', models.CharField(blank=True, default='', max_length=150)),
                ('sesion', models.CharField(blank=True, default='', max_length=64)),
                ('creado', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-creado'],
            },
        ),
        migrations.CreateModel(
            name='ContadorFolio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anio', models.PositiveIntegerField(unique=True)),
                ('ultimo', models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='VersionDocumento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('cotizacion', 'Cotización'), ('reporte', 'Reporte fotográfico'), ('croquis', 'Croquis de ubicación')], max_length=10)),
                ('documento_id', models.UUIDField()),
                ('revision', models.PositiveIntegerField()),
                ('datos', models.JSONField()),
                ('perfil', models.CharField(blank=True, default='', max_length=20)),
                ('cuenta_id', models.BigIntegerField(blank=True, null=True)),
                ('autor_nombre', models.CharField(blank=True, default='', max_length=150)),
                ('nota', models.CharField(blank=True, default='', max_length=200)),
                ('visible', models.BooleanField(default=True)),
                ('inicio', models.DateTimeField(default=django.utils.timezone.now)),
                ('creada', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-revision'],
            },
        ),
        # Machotes de la empresa: el mismo renglón, ahora compartido (con autor y «modificado por»).
        migrations.RemoveConstraint(model_name='machotecuenta', name='herramientas_machote_id'),
        migrations.RemoveConstraint(model_name='machotecuenta', name='herramientas_machote_nombre'),
        migrations.RenameModel(old_name='MachoteCuenta', new_name='Machote'),
        migrations.AddField(model_name='machote', name='autor_nombre', field=models.CharField(blank=True, default='', editable=False, max_length=150)),
        migrations.AddField(model_name='machote', name='autor_correo', field=models.CharField(blank=True, default='', editable=False, max_length=254)),
        migrations.AddField(model_name='machote', name='modificado_por_id', field=models.BigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='machote', name='modificado_por_nombre', field=models.CharField(blank=True, default='', max_length=150)),
        migrations.AddField(
            model_name='cotizacion',
            name='bloqueo_cuenta_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='bloqueo_desde',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='bloqueo_latido',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='bloqueo_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='bloqueo_sesion',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='conflicto',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='eliminado',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='eliminado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='eliminado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='folio_repetido',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='modificado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='modificado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='croquis',
            name='bloqueo_cuenta_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='bloqueo_desde',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='bloqueo_latido',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='bloqueo_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='croquis',
            name='bloqueo_sesion',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='croquis',
            name='conflicto',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='croquis',
            name='eliminado',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='eliminado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='eliminado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='croquis',
            name='modificado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='croquis',
            name='modificado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='reporte',
            name='bloqueo_cuenta_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='bloqueo_desde',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='bloqueo_latido',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='bloqueo_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='reporte',
            name='bloqueo_sesion',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='reporte',
            name='conflicto',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='reporte',
            name='eliminado',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='eliminado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='eliminado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='reporte',
            name='modificado_por_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reporte',
            name='modificado_por_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.RunPython(compartir, migrations.RunPython.noop),
        migrations.AlterField(model_name='machote', name='plantilla_id', field=models.UUIDField(default=uuid.uuid4, unique=True)),
        migrations.AddConstraint(
            model_name='cotizacion',
            constraint=models.UniqueConstraint(condition=models.Q(models.Q(('folio', ''), _negated=True), ('folio_repetido', False)), fields=('folio',), name='herramientas_cotizacion_folio_unico'),
        ),
        migrations.AddConstraint(
            model_name='borradorrecuperacion',
            constraint=models.UniqueConstraint(fields=('tipo', 'documento_id', 'cuenta_id', 'sesion'), name='herramientas_recuperacion_unica'),
        ),
        migrations.AddConstraint(
            model_name='machote',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('nombre'), name='herramientas_machote_nombre_empresa'),
        ),
        migrations.AddIndex(
            model_name='versiondocumento',
            index=models.Index(fields=['tipo', 'documento_id', 'visible'], name='herramientas_versiondoc_vis'),
        ),
        migrations.AddConstraint(
            model_name='versiondocumento',
            constraint=models.UniqueConstraint(fields=('tipo', 'documento_id', 'revision'), name='herramientas_versiondoc_revision'),
        ),
    ]
