"""Puntos topográficos: lectura y escritura de archivos de puntos en todos los órdenes comunes.

Campos de un punto: P = número o nombre, N = norte (Y), E = este (X), Z = elevación,
D = descripción (clave). Un orden se escribe con esas letras, por ejemplo «PNEZD» o «PENZD»;
también se aceptan X (= E) e Y (= N), así «PXYZD» es lo mismo que «PENZD».

La lectura:
- detecta codificación (UTF-8 con o sin BOM, UTF-16, Windows-1252), fin de línea y separador
  (coma, tabulador, punto y coma, barra vertical o espacios);
- admite coma decimal en archivos separados por punto y coma o tabulador;
- reconoce un renglón de encabezado y, si sus nombres lo dicen, el orden de las columnas;
- sugiere el orden cuando no hay encabezado con coordenadas UTM (en México el norte es
  mayor que el este);
- nunca pierde renglones en silencio: cada renglón descartado queda en `Lectura.descartados`
  con su número de línea y el motivo.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

__all__ = [
    "Punto",
    "Lectura",
    "ORDENES",
    "normalizar_orden",
    "decodificar",
    "detectar_separador",
    "sugerir_orden",
    "leer_puntos",
    "escribir_puntos",
    "numeros_repetidos",
    "renumerar",
]

#: En archivos separados por espacios un campo vacío no existe: la elevación faltante se escribe «-».
SIN_ELEVACION = "-"

TERRENO = "terreno"
PROYECTO = "proyecto"

#: Órdenes que ofrece la lista del diálogo, con su nombre visible.
ORDENES: dict[str, str] = {
    "PNEZD": "Punto, Norte (Y), Este (X), Elevación, Descripción",
    "PENZD": "Punto, Este (X), Norte (Y), Elevación, Descripción",
    "PNEZ": "Punto, Norte (Y), Este (X), Elevación",
    "PENZ": "Punto, Este (X), Norte (Y), Elevación",
    "NEZD": "Norte (Y), Este (X), Elevación, Descripción",
    "ENZD": "Este (X), Norte (Y), Elevación, Descripción",
    "NEZ": "Norte (Y), Este (X), Elevación",
    "ENZ": "Este (X), Norte (Y), Elevación",
    "PNE": "Punto, Norte (Y), Este (X)",
    "PEN": "Punto, Este (X), Norte (Y)",
    "PNED": "Punto, Norte (Y), Este (X), Descripción",
    "PEND": "Punto, Este (X), Norte (Y), Descripción",
}


@dataclass
class Punto:
    numero: str
    x: float  # este
    y: float  # norte
    z: float | None = None
    descripcion: str = ""
    tipo: str = TERRENO

    @property
    def este(self) -> float:
        return self.x

    @property
    def norte(self) -> float:
        return self.y


@dataclass
class Lectura:
    puntos: list[Punto]
    orden: str
    separador: str
    decimal: str
    codificacion: str
    encabezado: list[str] | None = None
    descartados: list[tuple[int, str, str]] = field(default_factory=list)  # (línea, texto, motivo)
    orden_sugerido: str | None = None
    orden_confianza: str = ""  # "encabezado", "utm", "" (dado por quien llama)


def normalizar_orden(orden: str) -> str:
    """Pasa X/Y a E/N, valida letras y repeticiones. Devuelve el orden en letras P, N, E, Z, D."""
    o = orden.strip().upper().replace("X", "E").replace("Y", "N")
    o = re.sub(r"[\s,;/_-]", "", o)
    if not o or any(c not in "PNEZD" for c in o):
        raise ValueError(f"Orden no válido: {orden!r}. Usa letras P, N, E, Z, D (o X, Y).")
    if len(set(o)) != len(o):
        raise ValueError(f"Orden con campos repetidos: {orden!r}.")
    if "N" not in o or "E" not in o:
        raise ValueError(f"El orden debe tener Norte y Este: {orden!r}.")
    return o


def decodificar(datos: bytes) -> tuple[str, str]:
    """Bytes → (texto, codificación)."""
    if datos.startswith(b"\xef\xbb\xbf"):
        return datos[3:].decode("utf-8"), "utf-8-sig"
    if datos.startswith((b"\xff\xfe", b"\xfe\xff")):
        return datos.decode("utf-16"), "utf-16"
    if len(datos) >= 4 and datos[1:2] == b"\x00" and datos[3:4] == b"\x00":
        return datos.decode("utf-16-le"), "utf-16-le"
    try:
        return datos.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return datos.decode("cp1252", errors="replace"), "cp1252"


_COMENTARIO = ("#", "//", "!", "*")


def _lineas_utiles(texto: str) -> list[tuple[int, str]]:
    salida = []
    for i, linea in enumerate(texto.splitlines(), start=1):
        limpia = linea.strip().strip("\x00")
        if not limpia or limpia.startswith(_COMENTARIO):
            continue
        salida.append((i, limpia))
    return salida


def detectar_separador(lineas: Sequence[str]) -> str:
    """Elige el separador que da el mismo número de campos (≥ 2) en más renglones."""
    muestra = list(lineas[:200])
    mejor, mejor_puntos = " ", -1.0
    for sep in ("\t", ";", ",", "|"):
        cuentas = [len(_partir(linea, sep)) for linea in muestra]
        utiles = [c for c in cuentas if c >= 2]
        if not utiles:
            continue
        moda = max(set(utiles), key=utiles.count)
        puntos = utiles.count(moda) / len(muestra) + 0.01 * moda
        if puntos > mejor_puntos:
            mejor, mejor_puntos = sep, puntos
    if mejor_puntos < 0.5:
        return " "
    return mejor


def _partir(linea: str, sep: str) -> list[str]:
    if sep == " ":
        return linea.split()
    return [c.strip() for c in next(csv.reader([linea], delimiter=sep, quotechar='"', skipinitialspace=True))]


def _a_numero(texto: str, decimal: str) -> float:
    t = texto.strip().replace(" ", "").replace(" ", "")
    if decimal == ",":
        t = t.replace(".", "").replace(",", ".")
    valor = float(t)
    if not math.isfinite(valor):
        raise ValueError("no finito")
    return valor


def _es_numero(texto: str, decimal: str) -> bool:
    try:
        _a_numero(texto, decimal)
        return True
    except ValueError:
        return False


_NOMBRES = {
    "P": ("p", "pt", "punto", "puntos", "no", "num", "numero", "número", "nombre", "name", "point", "id", "est", "estacion", "estación"),
    "N": ("n", "y", "norte", "north", "northing", "coordy", "coordenaday"),
    "E": ("e", "x", "este", "east", "easting", "coordx", "coordenadax"),
    "Z": ("z", "elev", "elevacion", "elevación", "elevation", "cota", "altura", "h", "alt"),
    "D": ("d", "desc", "descripcion", "descripción", "description", "code", "codigo", "código", "clave", "nota"),
}


def _orden_de_encabezado(campos: Sequence[str]) -> str | None:
    orden = ""
    for campo in campos:
        llave = re.sub(r"[^a-záéíóúñ]", "", campo.lower())
        letra = next((k for k, nombres in _NOMBRES.items() if llave in nombres), None)
        if letra is None or letra in orden:
            return None
        orden += letra
    try:
        return normalizar_orden(orden)
    except ValueError:
        return None


def sugerir_orden(filas: Sequence[Sequence[str]], decimal: str) -> tuple[str | None, str]:
    """Sugiere el orden por el contenido. Devuelve (orden, motivo) o (None, "")."""
    if not filas:
        return None, ""
    n = min(len(f) for f in filas)
    numericas = [all(_es_numero(f[i], decimal) for f in filas) for i in range(n)]
    tiene_p = not numericas[0] or (n >= 4 and all(numericas[:4]) and _parece_numeracion(filas))
    inicio = 1 if tiene_p else 0
    coords = [i for i in range(inicio, n) if numericas[i]]
    if len(coords) < 2:
        return None, ""
    a, b = coords[0], coords[1]
    va = [_a_numero(f[a], decimal) for f in filas]
    vb = [_a_numero(f[b], decimal) for f in filas]
    # UTM en México: este entre 160 000 y 840 000 m, norte entre 1 500 000 y 3 700 000 m.
    es_e = lambda v: 100_000 <= v <= 900_000
    es_n = lambda v: 1_000_000 <= v <= 10_000_000
    if all(map(es_e, va)) and all(map(es_n, vb)):
        primero = "EN"
    elif all(map(es_n, va)) and all(map(es_e, vb)):
        primero = "NE"
    else:
        return None, ""
    orden = ("P" if tiene_p else "") + primero
    resto = [i for i in range(b + 1, n)]
    if resto and numericas[resto[0]]:
        orden += "Z"
        resto = resto[1:]
    if resto:
        orden += "D"
    return orden, "utm"


def _parece_numeracion(filas) -> bool:
    try:
        valores = [float(f[0]) for f in filas]
    except ValueError:
        return False
    return all(v.is_integer() and 0 < v < 1e7 for v in valores)


def leer_puntos(
    datos: bytes | str,
    orden: str | None = None,
    separador: str | None = None,
    decimal: str | None = None,
    tipo: str = TERRENO,
    prefijo_numero: str = "",
) -> Lectura:
    """Lee un archivo de puntos.

    `orden=None` toma el orden del encabezado o lo sugiere por el contenido; si no puede, falla
    con un mensaje que pide elegirlo. Los renglones con campos de coordenadas no numéricos se
    descartan con su motivo. Sin columna P los puntos se numeran 1, 2, 3… en orden de lectura.
    """
    if isinstance(datos, bytes):
        texto, codificacion = decodificar(datos)
    else:
        texto, codificacion = datos, "texto"
    lineas = _lineas_utiles(texto)
    if not lineas:
        raise ValueError("El archivo no tiene renglones con datos.")
    sep = separador if separador is not None else detectar_separador([t for _, t in lineas])
    filas = [(i, t, _partir(t, sep)) for i, t in lineas]
    if decimal is None:
        decimal = "."
        if sep in (";", "\t", " ", "|"):
            muestra = [c for _, _, f in filas[:50] for c in f]
            if any(re.fullmatch(r"-?\d+,\d+", c) for c in muestra) and not any(re.fullmatch(r"-?\d+\.\d+", c) for c in muestra):
                decimal = ","
    encabezado = None
    orden_enc = None
    primera = filas[0][2]
    if sum(_es_numero(c, decimal) for c in primera) < 2:
        encabezado = primera
        orden_enc = _orden_de_encabezado(primera)
        filas = filas[1:]
    confianza = ""
    sugerido = None
    if orden is None:
        if orden_enc:
            orden, confianza = orden_enc, "encabezado"
        else:
            sugerido, confianza = sugerir_orden([f for _, _, f in filas[:200] if len(f) >= 2], decimal)
            if sugerido is None:
                raise ValueError("No se pudo reconocer el orden de las columnas: elígelo (por ejemplo PNEZD o PENZD).")
            orden = sugerido
    orden = normalizar_orden(orden)
    lectura = Lectura([], orden, sep, decimal, codificacion, encabezado, [], sugerido or orden_enc, confianza)
    automatico = 0
    for linea, original, campos in filas:
        minimo = len(orden) - (1 if orden.endswith("D") else 0)
        if len(campos) < minimo:
            lectura.descartados.append((linea, original, f"tiene {len(campos)} campos y el orden {orden} pide {minimo}"))
            continue
        valores: dict[str, str] = {}
        for k, letra in enumerate(orden):
            if letra == "D":
                valores["D"] = (" " if sep == " " else sep).join(campos[k:]).strip() if k < len(campos) else ""
            elif k < len(campos):
                valores[letra] = campos[k]
        try:
            x = _a_numero(valores["E"], decimal)
            y = _a_numero(valores["N"], decimal)
            z = _a_numero(valores["Z"], decimal) if "Z" in valores and valores["Z"] not in ("", SIN_ELEVACION) else None
        except ValueError:
            lectura.descartados.append((linea, original, "coordenada no numérica"))
            continue
        if "P" in valores:
            numero = valores["P"].strip()
            if not numero:
                lectura.descartados.append((linea, original, "punto sin número"))
                continue
        else:
            automatico += 1
            numero = str(automatico)
        lectura.puntos.append(Punto(prefijo_numero + numero, x, y, z, valores.get("D", "").strip().strip('"'), tipo))
    if not lectura.puntos:
        raise ValueError("Ningún renglón tiene coordenadas válidas con el orden " + orden + ".")
    return lectura


def _fmt(valor: float | None, decimales: int, decimal: str) -> str:
    if valor is None:
        return ""
    texto = f"{valor:.{decimales}f}"
    if texto.startswith("-") and float(texto) == 0.0:
        texto = texto[1:]
    return texto.replace(".", ",") if decimal == "," else texto


def escribir_puntos(
    puntos: Iterable[Punto],
    orden: str = "PENZD",
    separador: str = ",",
    decimales: int = 3,
    decimales_z: int | None = None,
    decimal: str = ".",
    encabezado: bool = False,
    fin_de_linea: str = "\r\n",
    codificacion: str = "utf-8",
) -> bytes:
    """Escribe un archivo de puntos. Una descripción que contiene el separador va entre comillas.

    El punto sin elevación escribe el campo Z vacío («-» si el separador es espacio, para no
    correr las columnas). Con separador espacio la descripción puede llevar espacios (va al final
    del renglón).
    """
    orden = normalizar_orden(orden)
    if decimal == "," and separador == ",":
        raise ValueError("Con coma decimal el separador no puede ser coma.")
    dz = decimales if decimales_z is None else decimales_z
    nombres = {"P": "Punto", "N": "Norte", "E": "Este", "Z": "Elevacion", "D": "Descripcion"}
    salida = io.StringIO()
    if encabezado:
        salida.write(separador.join(nombres[c] for c in orden) + fin_de_linea)
    for p in puntos:
        campos = []
        for letra in orden:
            if letra == "P":
                campos.append(_campo_texto(p.numero, separador))
            elif letra == "N":
                campos.append(_fmt(p.y, decimales, decimal))
            elif letra == "E":
                campos.append(_fmt(p.x, decimales, decimal))
            elif letra == "Z":
                campos.append(SIN_ELEVACION if p.z is None and separador == " " else _fmt(p.z, dz, decimal))
            else:
                campos.append(p.descripcion if separador == " " and letra == orden[-1] else _campo_texto(p.descripcion, separador))
        salida.write(separador.join(campos).rstrip() + fin_de_linea if separador == " " else separador.join(campos) + fin_de_linea)
    return salida.getvalue().encode(codificacion)


def _campo_texto(texto: str, separador: str) -> str:
    if separador != " " and (separador in texto or '"' in texto):
        return '"' + texto.replace('"', '""') + '"'
    if separador == " " and " " in texto:
        return texto.replace(" ", "_")
    return texto


def numeros_repetidos(puntos: Iterable[Punto]) -> dict[str, int]:
    """Números que aparecen más de una vez y cuántas veces."""
    cuenta: dict[str, int] = {}
    for p in puntos:
        cuenta[p.numero] = cuenta.get(p.numero, 0) + 1
    return {k: v for k, v in cuenta.items() if v > 1}


def renumerar(puntos: Sequence[Punto], inicio: int = 1, incremento: int = 1, prefijo: str = "") -> list[Punto]:
    """Copia de los puntos numerados en orden: prefijo + inicio, inicio + incremento, …"""
    if incremento == 0:
        raise ValueError("El incremento no puede ser cero.")
    return [Punto(f"{prefijo}{inicio + k * incremento}", p.x, p.y, p.z, p.descripcion, p.tipo) for k, p in enumerate(puntos)]
