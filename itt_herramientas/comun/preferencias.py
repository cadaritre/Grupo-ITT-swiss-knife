"""Preferencias recordadas por cuenta (zona UTM, mapa base, opciones de reportes, croquis, triangulación)."""
from __future__ import annotations

from copy import deepcopy

from django.utils import timezone

from ..dominio.almacenamiento import DEFAULT_SETTINGS, fusionar
from ..dominio.location_sketch import MAP_LAYERS
from ..models import Preferencias

INTERVALOS = ("Automática", "1 m", "2 m", "5 m", "10 m", "20 m", "50 m")
SECCIONES = ("reports", "geospatial", "sketches", "triangulation")


class PreferenciaInvalida(ValueError):
    pass


def _bool(valor, nombre):
    if not isinstance(valor, bool):
        raise PreferenciaInvalida(f"«{nombre}» debe ser sí o no.")
    return valor


def _capa(valor, nombre):
    if valor not in MAP_LAYERS:
        raise PreferenciaInvalida(f"Mapa base desconocido en «{nombre}»: {valor}")
    return valor


def _zona(valor, nombre):
    if isinstance(valor, bool) or not isinstance(valor, int) or not 1 <= valor <= 60:
        raise PreferenciaInvalida("La zona UTM debe ser un número de 1 a 60.")
    return valor


def _hemisferio(valor, nombre):
    if valor not in ("N", "S"):
        raise PreferenciaInvalida("El hemisferio debe ser N o S.")
    return valor


def _intervalo(valor, nombre):
    if valor not in INTERVALOS:
        raise PreferenciaInvalida(f"Equidistancia desconocida: {valor}")
    return valor


#: sección -> clave -> validador. Lo que no está aquí (p. ej. `reports.open_pdf`, de escritorio) no se guarda.
ESQUEMA = {
    "reports": {"include_map": _bool, "include_credentials": _bool},
    "geospatial": {"utm_zone": _zona, "hemisphere": _hemisferio, "map_layer": _capa, "labels": _bool, "hatches": _bool, "clamp_to_ground": _bool},
    "sketches": {"map_layer": _capa, "contour_interval": _intervalo},
    "triangulation": {"draw_preview": _bool},
}


def de_fabrica() -> dict:
    return {s: {k: deepcopy(DEFAULT_SETTINGS[s][k]) for k in ESQUEMA[s]} for s in SECCIONES}


def validar(valores: dict, estricto: bool = True) -> dict:
    """Valida `{sección: {clave: valor}}`. Estricto: una clave desconocida es error; si no, se ignora (importación)."""
    limpio: dict = {}
    for seccion, contenido in valores.items():
        if seccion not in ESQUEMA:
            if estricto:
                raise PreferenciaInvalida(f"Sección desconocida: {seccion}")
            continue
        if not isinstance(contenido, dict):
            raise PreferenciaInvalida(f"La sección «{seccion}» debe ser un conjunto de valores.")
        for clave, valor in contenido.items():
            if clave not in ESQUEMA[seccion]:
                if estricto:
                    raise PreferenciaInvalida(f"Preferencia desconocida: {seccion}.{clave}")
                continue
            limpio.setdefault(seccion, {})[clave] = ESQUEMA[seccion][clave](valor, f"{seccion}.{clave}")
    return limpio


def obtener(cuenta_id: int) -> dict:
    """Preferencias de la cuenta sobre las de fábrica."""
    fila = Preferencias.objects.filter(cuenta_id=cuenta_id).first()
    return fusionar(de_fabrica(), validar(fila.datos, estricto=False) if fila else {})


def guardar(cuenta_id: int, valores: dict, estricto: bool = True) -> dict:
    nuevos = validar(valores, estricto)
    fila, _ = Preferencias.objects.get_or_create(cuenta_id=cuenta_id)
    fila.datos = fusionar(validar(fila.datos, estricto=False), nuevos)
    fila.actualizado = timezone.now()
    fila.save()
    return obtener(cuenta_id)
