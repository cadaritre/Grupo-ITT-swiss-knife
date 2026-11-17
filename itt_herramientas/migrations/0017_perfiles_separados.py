"""Cada perfil corporativo es un espacio de documentos aparte (decisión del propietario, 17-11-2026).

Grupo ITT y TresVizo dejan de compartir listas y folios: cada perfil tiene su serie COT-AAAA-NNNN por año. Esta migración:

1. Folios: ninguno cambia (alguno ya puede estar con un cliente). La unicidad pasa a ser (perfil, folio). Un folio que estaba marcado
   como repetido solo porque otra cotización del OTRO perfil lo tenía deja de estarlo; dentro de un mismo perfil, el más antiguo se queda
   con el folio y los demás siguen marcados para que el propietario les asigne uno nuevo.
2. Contadores: uno por perfil y año, que nunca vuelve a dar un folio que ya salió (`separar`): sigue desde el mayor folio del perfil o
   desde el mayor que el contador de la empresa ya dio y que no es del otro perfil (uno borrado para siempre cuenta para los dos).
3. Índices (perfil, cambio) de los tres documentos: las listas y la sincronización de los teléfonos filtran por perfil.

Es rápida (recorre solo las cotizaciones con folio) y reversible: al revertir, los folios vuelven a ser únicos en la empresa (el más
antiguo conserva el suyo y los demás quedan marcados; nunca se renumera) y queda un contador por año, el mayor de los perfiles.
"""

import re

from django.db import migrations, models

_FOLIO = re.compile(r"^COT-(\d{4})-(\d{1,6})$")


def _marcar(Cotizacion, llave) -> None:
    """Deja un solo folio sin marca en cada grupo (`llave(perfil, folio)`), sin quitársela a quien ya la tiene: si en el grupo hay una sin
    marca (aunque no sea la más antigua: la que conservaba el folio se borró para siempre y otra lo tomó después), se queda esa y las demás
    sin marca, si las hay, se marcan; si todas estaban marcadas, la más antigua deja de estarlo. Las marcadas siguen marcadas."""
    grupos = {}
    for pk, perfil, folio, repetido in Cotizacion.objects.exclude(folio="").order_by("creada", "id").values_list("pk", "perfil", "folio", "folio_repetido"):
        grupos.setdefault(llave(perfil, folio), []).append((pk, repetido))
    marcar, desmarcar = [], []
    for filas in grupos.values():
        libres = [pk for pk, repetido in filas if not repetido]
        if libres:
            marcar += libres[1:]
        else:
            desmarcar.append(filas[0][0])
    Cotizacion.objects.filter(pk__in=marcar).update(folio_repetido=True)
    Cotizacion.objects.filter(pk__in=desmarcar).update(folio_repetido=False)


def _numeros(Cotizacion) -> dict:
    """{(perfil, año): {números de folio}} de los datos (también los de la papelera)."""
    numeros = {}
    for perfil, folio in Cotizacion.objects.filter(folio__startswith="COT-").values_list("perfil", "folio"):
        m = _FOLIO.match(folio)
        if m:
            numeros.setdefault((perfil, int(m.group(1))), set()).add(int(m.group(2)))
    return numeros


def separar(apps, schema_editor):
    """Contador de cada perfil y año: sigue desde el mayor de estos dos números, para no volver a dar nunca un folio que ya salió:

    - el mayor folio que ese perfil tiene hoy;
    - el mayor número que el contador de la empresa ya dio y que no es de un folio que hoy tiene el OTRO perfil. Uno que ya no está en
      ninguno (una cotización borrada para siempre de la papelera, cuyo PDF pudo llegar a un cliente) no se sabe de qué perfil era: cuenta
      para los dos.

    Con Grupo ITT en 0001–0004 y TresVizo en 0005 (contador de la empresa en 5), Grupo ITT sigue en 0005 y TresVizo en 0006. Si además se
    hubiera borrado para siempre la 0007 (contador en 7), los dos seguirían en 0008.
    """
    Cotizacion = apps.get_model("herramientas", "Cotizacion")
    ContadorFolio = apps.get_model("herramientas", "ContadorFolio")
    _marcar(Cotizacion, lambda perfil, folio: (perfil, folio))
    viejos = {}
    for anio, ultimo in ContadorFolio.objects.values_list("anio", "ultimo"):
        viejos[anio] = max(viejos.get(anio, 0), ultimo)
    numeros = _numeros(Cotizacion)
    perfiles = {"grupo_itt", "tresvizo"} | {p for p, _ in numeros}
    anios = set(viejos) | {a for _, a in numeros}
    contadores = []
    for perfil in sorted(perfiles):
        for anio in sorted(anios):
            propios = numeros.get((perfil, anio), set())
            ajenos = set().union(*(n for (p, a), n in numeros.items() if a == anio and p != perfil))
            dado = viejos.get(anio, 0)
            while dado and dado in ajenos:
                dado -= 1
            ultimo = max(max(propios, default=0), dado)
            if ultimo:
                contadores.append(ContadorFolio(perfil=perfil, anio=anio, ultimo=ultimo))
    ContadorFolio.objects.all().delete()
    ContadorFolio.objects.bulk_create(contadores)


def juntar(apps, schema_editor):
    Cotizacion = apps.get_model("herramientas", "Cotizacion")
    ContadorFolio = apps.get_model("herramientas", "ContadorFolio")
    _marcar(Cotizacion, lambda perfil, folio: folio)
    por_anio = {}
    for anio, ultimo in ContadorFolio.objects.values_list("anio", "ultimo"):
        por_anio[anio] = max(por_anio.get(anio, 0), ultimo)
    for (_, anio), n in _numeros(Cotizacion).items():
        por_anio[anio] = max(por_anio.get(anio, 0), max(n))
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
