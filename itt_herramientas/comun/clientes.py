"""Clientes que el equipo ya escribió (en los reportes y las cotizaciones de la empresa): las sugerencias del campo «Cliente».

No hay catálogo que administrar: un nombre nuevo se vuelve sugerencia en cuanto se guarda un documento con él. Se quitan
los repetidos sin distinguir mayúsculas ni acentos («Agrícola Norte» = «AGRICOLA NORTE») y queda la forma más reciente.
"""
from __future__ import annotations

import unicodedata

from ..models import Cotizacion, Reporte

MAXIMO = 300


def clave(texto: str) -> str:
    """Forma para comparar: sin acentos, en minúsculas y con un solo espacio entre palabras."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())


def sugerencias(cuenta_id: int | None = None, maximo: int = MAXIMO, perfil: str | None = None) -> list[str]:
    """Clientes de los documentos de la empresa (son compartidos), del usado más recientemente al más viejo, sin repetidos. Con `perfil`,
    solo los de ese perfil corporativo (cada perfil es un espacio aparte). `cuenta_id` se acepta por compatibilidad y ya no filtra."""
    filas: list[tuple] = []
    for modelo in (Reporte, Cotizacion):
        consulta = modelo.objects.filter(eliminado__isnull=True) if perfil is None else modelo.objects.filter(perfil=perfil, eliminado__isnull=True)
        filas += list(consulta.exclude(cliente="").order_by("-actualizada")
                      .values_list("actualizada", "cliente")[:maximo * 3])
    filas.sort(key=lambda f: f[0], reverse=True)
    vistos, salida = set(), []
    for _, nombre in filas:
        nombre = " ".join(nombre.split())
        k = clave(nombre)
        if k and k not in vistos:
            vistos.add(k)
            salida.append(nombre)
            if len(salida) >= maximo:
                break
    return salida
