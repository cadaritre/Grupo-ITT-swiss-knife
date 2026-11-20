"""CAD: proyectos y carpetas (19-11-2026). Aditiva: una tabla nueva y dos columnas que admiten vacío en `Plano`.

Cada etiqueta «Proyecto o cliente» distinta de los planos (sin distinguir mayúsculas, acentos ni espacios de más) pasa a ser un proyecto con
ese nombre (la forma escrita más reciente), y sus planos, también los de la papelera, quedan dentro. Los planos sin etiqueta se quedan en
la raíz de «Planos». `Plano.proyecto` sigue siendo el nombre del proyecto (las variantes de una etiqueta quedan con la forma del proyecto) y
cada proyecto guarda la etiqueta de la que salió (`CarpetaCad.etiqueta`). Deshacerla quita las carpetas y las columnas nuevas; cada plano
conserva su etiqueta, como antes. Nada se borra ni se mueve en el disco.
"""

import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
import uuid
import re
import unicodedata

from django.db import migrations, models


def _clave(texto):
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())


def _nombre(texto):
    # Copia fija de cad.carpetas.nombre_limpio (una migración no importa código que pueda cambiar).
    texto = unicodedata.normalize("NFC", str(texto or ""))
    texto = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', " ", texto)
    texto = " ".join(texto.split()).strip(" .")
    return texto[:150].strip(" .") or "Proyecto"


def etiquetas_a_proyectos(apps, schema_editor):
    Plano = apps.get_model("herramientas", "Plano")
    CarpetaCad = apps.get_model("herramientas", "CarpetaCad")
    grupos = {}                                   # clave → [nombre más reciente, {etiquetas tal cual}]
    for etiqueta in Plano.objects.exclude(proyecto="").order_by("-actualizado").values_list("proyecto", flat=True).iterator():
        k = _clave(etiqueta)
        if not k:
            continue
        g = grupos.setdefault(k, [" ".join(etiqueta.split()), set()])
        g[1].add(etiqueta)
    usados = set()
    for k in sorted(grupos):
        visto, etiquetas = grupos[k]
        nombre, n = _nombre(visto), 2
        base = nombre
        while nombre.lower() in usados:
            nombre = f"{base[:145]} ({n})"
            n += 1
        usados.add(nombre.lower())
        c = CarpetaCad.objects.create(nombre=nombre, etiqueta=visto[:160])
        Plano.objects.filter(proyecto__in=list(etiquetas)).update(carpeta=c, proyecto=nombre[:160])




class Migration(migrations.Migration):

    dependencies = [
        ('herramientas', '0019_cad_plantillas'),
    ]

    operations = [
        migrations.AddField(
            model_name='plano',
            name='lote_papelera',
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name='CarpetaCad',
            fields=[
                ('autor_nombre', models.CharField(blank=True, default='', editable=False, max_length=150)),
                ('autor_correo', models.CharField(blank=True, default='', editable=False, max_length=254)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('cuenta_id', models.BigIntegerField(blank=True, null=True)),
                ('nombre', models.CharField(max_length=150)),
                ('cliente', models.CharField(blank=True, max_length=160)),
                ('creada', models.DateTimeField(default=django.utils.timezone.now)),
                ('actualizada', models.DateTimeField(default=django.utils.timezone.now)),
                ('eliminada', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('eliminada_por_id', models.BigIntegerField(blank=True, null=True)),
                ('eliminada_por_nombre', models.CharField(blank=True, max_length=150)),
                ('lote_papelera', models.UUIDField(blank=True, db_index=True, null=True)),
                ('etiqueta', models.CharField(blank=True, max_length=160)),
                ('padre', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='hijas', to='herramientas.carpetacad')),
            ],
            options={
                'ordering': ['nombre'],
            },
        ),
        migrations.AddField(
            model_name='plano',
            name='carpeta',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='planos', to='herramientas.carpetacad'),
        ),
        migrations.AddConstraint(
            model_name='carpetacad',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('nombre'), models.F('padre'), condition=models.Q(('eliminada__isnull', True), ('padre__isnull', False)), name='herramientas_carpetacad_nombre'),
        ),
        migrations.AddConstraint(
            model_name='carpetacad',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('nombre'), condition=models.Q(('eliminada__isnull', True), ('padre__isnull', True)), name='herramientas_carpetacad_proyecto'),
        ),
        migrations.RunPython(etiquetas_a_proyectos, migrations.RunPython.noop, elidable=False),
    ]
