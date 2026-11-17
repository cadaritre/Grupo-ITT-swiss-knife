"""Cada perfil corporativo es un espacio de documentos aparte (decisión del propietario, 17-11-2026).

Grupo ITT y TresVizo dejan de compartir listas y folios: cada perfil tiene su serie COT-AAAA-NNNN por año. Esta migración:

1. Folios: ninguno cambia (alguno ya puede estar con un cliente). La unicidad pasa a ser (perfil, folio). Un folio que estaba marcado
   como repetido solo porque otra cotización del OTRO perfil lo tenía deja de estarlo; dentro de un mismo perfil, el más antiguo se queda
   con el folio y los demás siguen marcados para que el propietario les asigne uno nuevo.
2. Contadores: uno por perfil y año, que sigue desde el mayor folio que ya tiene ese perfil en ese año (también los de la papelera).
3. Índices (perfil, cambio) de los tres documentos: las listas y la sincronización de los teléfonos filtran por perfil.

Es rápida (recorre solo las cotizaciones con folio) y reversible: al revertir, los folios vuelven a ser únicos en la empresa (el más
antiguo conserva el suyo y los demás quedan marcados; nunca se renumera) y queda un contador por año, el mayor de los perfiles.
"""

import re

from django.db import migrations, models

_FOLIO = re.compile(r"^COT-(\d{4})-(\d{1,6})$")


def _marcar(Cotizacion, llave) -> None:
    """Deja marcado como repetido todo folio que no es el más antiguo de su grupo (`llave(cotización)`); el más antiguo, sin marca."""
    vistos = set()
    marcar, desmarcar = [], []
    for pk, perfil, folio, repetido in Cotizacion.objects.exclude(folio="").order_by("creada", "id").values_list("pk", "perfil", "folio", "folio_repetido"):
        k = llave(perfil, folio)
        if k in vistos:
            if not repetido:
                marcar.append(pk)
        else:
            vistos.add(k)
            if repetido:
                desmarcar.append(pk)
    Cotizacion.objects.filter(pk__in=marcar).update(folio_repetido=True)
    Cotizacion.objects.filter(pk__in=desmarcar).update(folio_repetido=False)


def _mayores(Cotizacion) -> dict:
    """{(perfil, año): mayor número de folio} de los datos."""
    mayores = {}
    for perfil, folio in Cotizacion.objects.filter(folio__startswith="COT-").values_list("perfil", "folio"):
        m = _FOLIO.match(folio)
        if m:
            k = (perfil, int(m.group(1)))
            mayores[k] = max(mayores.get(k, 0), int(m.group(2)))
    return mayores


def separar(apps, schema_editor):
    Cotizacion = apps.get_model("herramientas", "Cotizacion")
    ContadorFolio = apps.get_model("herramientas", "ContadorFolio")
    _marcar(Cotizacion, lambda perfil, folio: (perfil, folio))
    ContadorFolio.objects.all().delete()
    ContadorFolio.objects.bulk_create([ContadorFolio(perfil=perfil, anio=anio, ultimo=n) for (perfil, anio), n in sorted(_mayores(Cotizacion).items())])


def juntar(apps, schema_editor):
    Cotizacion = apps.get_model("herramientas", "Cotizacion")
    ContadorFolio = apps.get_model("herramientas", "ContadorFolio")
    _marcar(Cotizacion, lambda perfil, folio: folio)
    por_anio = {}
    for anio, ultimo in ContadorFolio.objects.values_list("anio", "ultimo"):
        por_anio[anio] = max(por_anio.get(anio, 0), ultimo)
    for (_, anio), n in _mayores(Cotizacion).items():
        por_anio[anio] = max(por_anio.get(anio, 0), n)
    ContadorFolio.objects.all().delete()
    ContadorFolio.objects.bulk_create([ContadorFolio(perfil="grupo_itt", anio=anio, ultimo=n) for anio, n in sorted(por_anio.items())])


class Migration(migrations.Migration):

    dependencies = [
        ("herramientas", "0016_documentos_equipo"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="cotizacion", name="herramientas_cotizacion_folio_unico"),
        migrations.AddField(model_name="contadorfolio", name="perfil", field=models.CharField(default="grupo_itt", max_length=20)),
        migrations.AlterField(model_name="contadorfolio", name="anio", field=models.PositiveIntegerField()),
        migrations.AddConstraint(
            model_name="contadorfolio",
            constraint=models.UniqueConstraint(fields=("perfil", "anio"), name="herramientas_contadorfolio_perfil_anio"),
        ),
        migrations.RunPython(separar, juntar),
        migrations.AddConstraint(
            model_name="cotizacion",
            constraint=models.UniqueConstraint(condition=models.Q(models.Q(("folio", ""), _negated=True), ("folio_repetido", False)),
                                               fields=("perfil", "folio"), name="herramientas_cotizacion_folio_perfil"),
        ),
        migrations.AddIndex(model_name="cotizacion", index=models.Index(fields=["perfil", "cambio"], name="herramientas_cot_perfil_cambio")),
        migrations.AddIndex(model_name="croquis", index=models.Index(fields=["perfil", "cambio"], name="herramientas_crq_perfil_cambio")),
        migrations.AddIndex(model_name="reporte", index=models.Index(fields=["perfil", "cambio"], name="herramientas_rep_perfil_cambio")),
    ]
