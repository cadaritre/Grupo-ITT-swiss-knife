"""Corrector ortográfico: diccionario personal por cuenta y diccionario técnico global."""
from __future__ import annotations

from django.utils import timezone

from ..dominio.spellcheck import WORD_PATTERN, BilingualSpellService
from ..models import DiccionarioPersonal, TerminoTecnico


class PalabraInvalida(ValueError):
    pass


def normalizar_palabra(palabra: str) -> str:
    valor = (palabra or "").strip().casefold()
    coincidencia = WORD_PATTERN.fullmatch(valor)
    if not valor or len(valor) > 60 or coincidencia is None:
        raise PalabraInvalida("Escribe una sola palabra (letras, sin espacios ni números).")
    return valor


def terminos_globales(idioma: str) -> set[str]:
    return set(TerminoTecnico.objects.filter(idioma__in=[idioma, "ambos"]).values_list("palabra", flat=True))


def palabras_personales(cuenta_id: int) -> dict[str, list[str]]:
    fila = DiccionarioPersonal.objects.filter(cuenta_id=cuenta_id).first()
    datos = fila.palabras if fila else {}
    return {i: sorted({str(p).casefold() for p in datos.get(i, [])}) for i in ("es", "en")}


def corrector(cuenta_id: int, idioma: str = "es") -> BilingualSpellService:
    return BilingualSpellService(idioma, palabras_personales=palabras_personales(cuenta_id), terminos_tecnicos=terminos_globales(idioma))


def agregar_palabra(cuenta_id: int, palabra: str, idioma: str = "es") -> dict[str, list[str]]:
    """Agrega una palabra al diccionario personal (del idioma indicado)."""
    valor = normalizar_palabra(palabra)
    if idioma not in ("es", "en"):
        raise PalabraInvalida("Idioma desconocido.")
    actuales = palabras_personales(cuenta_id)
    if valor not in actuales[idioma]:
        actuales[idioma] = sorted([*actuales[idioma], valor])
    guardar_palabras(cuenta_id, actuales)
    return actuales


def quitar_palabra(cuenta_id: int, palabra: str, idioma: str = "es") -> dict[str, list[str]]:
    valor = normalizar_palabra(palabra)
    actuales = palabras_personales(cuenta_id)
    actuales[idioma] = [p for p in actuales.get(idioma, []) if p != valor]
    guardar_palabras(cuenta_id, actuales)
    return actuales


def guardar_palabras(cuenta_id: int, palabras: dict[str, list[str]]) -> None:
    fila, _ = DiccionarioPersonal.objects.get_or_create(cuenta_id=cuenta_id)
    fila.palabras = {i: sorted(set(palabras.get(i, []))) for i in ("es", "en")}
    fila.actualizado = timezone.now()
    fila.save()


def fusionar_palabras(cuenta_id: int, nuevas: dict[str, list[str]]) -> int:
    """Suma palabras (importación del escritorio). Ignora las inválidas. Devuelve cuántas eran nuevas."""
    actuales = palabras_personales(cuenta_id)
    agregadas = 0
    for idioma in ("es", "en"):
        for palabra in nuevas.get(idioma, []):
            try:
                valor = normalizar_palabra(str(palabra))
            except PalabraInvalida:
                continue
            if valor not in actuales[idioma]:
                actuales[idioma].append(valor)
                agregadas += 1
    guardar_palabras(cuenta_id, actuales)
    return agregadas


def agregar_termino(palabra: str, idioma: str, cuenta_id: int | None = None) -> TerminoTecnico:
    valor = normalizar_palabra(palabra)
    if idioma not in ("es", "en", "ambos"):
        raise PalabraInvalida("Idioma desconocido.")
    termino, _ = TerminoTecnico.objects.get_or_create(palabra=valor, idioma=idioma, defaults={"creado_por": cuenta_id})
    return termino


def quitar_termino(termino_id: int) -> bool:
    borrados, _ = TerminoTecnico.objects.filter(pk=termino_id).delete()
    return bool(borrados)
