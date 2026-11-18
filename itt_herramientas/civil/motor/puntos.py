"""Puntos topográficos: lectura y escritura de archivos de puntos con cualquier delimitador y columnas en cualquier orden.

Campos de un punto: P = número o nombre, N = norte (Y), E = este (X), Z = elevación, D = descripción (clave), y para archivos en
geográficas LAT = latitud y LON = longitud (se convierten a la zona UTM del plano). «-» es una columna que no se lee.

Un **formato** dice cómo está escrito el archivo, como los formatos de archivo de puntos de Civil 3D:
- delimitador: coma, punto y coma, tabulador, espacios o cualquier texto que escriba la persona («|», «::», «/»…);
- «juntar»: varios delimitadores seguidos cuentan como uno (los campos vacíos no existen);
- calificador de texto (`"`): lo que va entre calificadores es un solo campo aunque traiga el delimitador; dos seguidos son uno literal;
- separador decimal: punto o coma (la coma decimal es común en México con «;»);
- columnas: una por campo, en el orden del archivo (P, N, E, Z, D, LAT, LON o «-»);
- renglones que se saltan al principio (encabezados).

Un orden de letras («PNEZD», «PENZD», también con X = E e Y = N) es la forma corta de unas columnas.

La lectura:
- detecta codificación (UTF-8 con o sin BOM, UTF-16, Windows-1252), fin de línea, delimitador y separador decimal;
- reconoce un renglón de encabezado y, si sus nombres lo dicen, las columnas;
- sugiere las columnas cuando no hay encabezado: con coordenadas UTM (en México el norte es mayor que el este) o geográficas;
- da los primeros renglones ya partidos en campos, para la vista previa;
- numera los puntos sin número con el siguiente número libre;
- nunca pierde renglones en silencio: cada renglón descartado queda en `Lectura.descartados` con su número de línea y el motivo.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

__all__ = [
    "Punto",
    "Lectura",
    "ORDENES",
    "COLUMNAS",
    "DELIMITADORES",
    "normalizar_orden",
    "normalizar_columnas",
    "normalizar_formato",
    "decodificar",
    "partir",
    "detectar_separador",
    "sugerir_orden",
    "leer_angulo",
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

#: Columnas posibles de un archivo de puntos, con su nombre visible (el de la lista sobre cada columna de la vista previa).
COLUMNAS: dict[str, str] = {
    "P": "Número",
    "N": "Norte (Y)",
    "E": "Este (X)",
    "Z": "Elevación",
    "D": "Descripción",
    "LAT": "Latitud",
    "LON": "Longitud",
    "-": "Ignorar",
}

#: Delimitadores con nombre; cualquier otro texto también vale.
DELIMITADORES: dict[str, str] = {",": "coma", ";": "punto y coma", "\t": "tabulador", " ": "espacios", "|": "barra"}

#: Encabezado que escribe la exportación, por columna.
NOMBRES_COLUMNA = {"P": "Punto", "N": "Norte", "E": "Este", "Z": "Elevacion", "D": "Descripcion", "LAT": "Latitud", "LON": "Longitud"}


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
    orden: str  # las columnas como letras («PNEZD») o, con LAT/LON o «-», separadas por comas
    separador: str  # el delimitador usado
    decimal: str
    codificacion: str
    encabezado: list[str] | None = None
    descartados: list[tuple[int, str, str]] = field(default_factory=list)  # (línea, texto, motivo)
    orden_sugerido: str | None = None
    orden_confianza: str = ""  # "encabezado", "utm", "geograficas", "" (dado por quien llama)
    columnas: list[str] = field(default_factory=list)
    juntar: bool = False
    calificador: str = '"'
    saltar: int = 0
    campos: int = 0  # campos del renglón más largo de la muestra
    muestra: list[tuple[int, list[str]]] = field(default_factory=list)  # primeros renglones ya partidos (línea, campos), sin el encabezado
    geograficas: bool = False
    zonas: dict[int, int] = field(default_factory=dict)  # con geográficas: zona UTM de las longitudes → cuántos puntos
    sin_numero: int = 0  # puntos que tomaron el siguiente número libre
    avisos: list[str] = field(default_factory=list)


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


def normalizar_columnas(columnas: Sequence[str] | str) -> list[str]:
    """Columnas válidas: cada una P, N, E, Z, D, LAT, LON o «-» (X = E, Y = N), sin repetir, con Norte y Este o con Latitud y Longitud.

    Un texto se toma como orden de letras («PNEZD») o como lista separada por comas («P,LAT,LON,Z»).
    """
    if isinstance(columnas, str):
        texto = columnas.strip().upper()
        columnas = [c for c in re.split(r"[\s,;]+", texto) if c] if ("," in texto or "LAT" in texto or "LON" in texto) else list(normalizar_orden(texto))
    salida = []
    for c in columnas:
        k = str(c).strip().upper()
        k = {"X": "E", "Y": "N", "": "-", "IGNORAR": "-", "LONG": "LON", "LATITUD": "LAT", "LONGITUD": "LON"}.get(k, k)
        if k not in COLUMNAS:
            raise ValueError(f"Columna no válida: {c!r}.")
        if k != "-" and k in salida:
            raise ValueError(f"La columna {COLUMNAS[k]} está dos veces.")
        salida.append(k)
    utm, geo = {"N", "E"} <= set(salida), {"LAT", "LON"} <= set(salida)
    if utm and geo:
        raise ValueError("Usa Norte y Este, o Latitud y Longitud; no las dos.")
    if not (utm or geo):
        if any(k in salida for k in ("LAT", "LON")):
            raise ValueError("Faltan la latitud o la longitud.")
        raise ValueError("Faltan las columnas del Norte (Y) y del Este (X).")
    if (("N" in salida) ^ ("E" in salida)) or (("LAT" in salida) ^ ("LON" in salida)):
        raise ValueError("Usa Norte y Este, o Latitud y Longitud; no las dos.")
    while salida and salida[-1] == "-":
        salida.pop()
    return salida


def _orden_texto(columnas: Sequence[str]) -> str:
    return "".join(columnas) if all(len(c) == 1 and c != "-" for c in columnas) else ",".join(columnas)


def normalizar_formato(d: dict, uso: str = "importar") -> dict:
    """Un formato de archivo de puntos (el que la persona guarda con nombre) limpio y completo.

    Campos: delimitador (texto; "" = reconocer, solo al importar), juntar, calificador ("" o un carácter), decimal ("." o ","; "" =
    reconocer al importar), columnas (lista; vacía = reconocer al importar), saltar (renglones), y al exportar encabezado, decimales,
    decimales_z y decimales_geo.
    """
    if not isinstance(d, dict):
        raise ValueError("El formato no es válido.")
    importar = uso == "importar"
    delim = str(d.get("delimitador", "" if importar else ","))
    if len(delim) > 8:
        raise ValueError("El delimitador es de 8 caracteres como máximo.")
    if not delim and not importar:
        raise ValueError("Elige el delimitador.")
    calif = str(d.get("calificador", '"'))
    if len(calif) > 1:
        raise ValueError("El calificador de texto es un solo carácter.")
    if calif and delim and calif in delim:
        raise ValueError("El calificador de texto no puede ser parte del delimitador.")
    decimal = str(d.get("decimal", "" if importar else "."))
    if decimal not in (".", ",") and not (importar and decimal == ""):
        raise ValueError("El separador decimal es punto o coma.")
    if decimal == "," and "," in delim:
        raise ValueError("Con coma decimal el delimitador no puede ser coma.")
    cols = d.get("columnas") or []
    if not isinstance(cols, (list, tuple)):
        raise ValueError("Las columnas no son válidas.")
    if len(cols) > 40:
        raise ValueError("Son demasiadas columnas.")
    if cols or not importar:
        cols = normalizar_columnas(cols or "PNEZD")
    try:
        saltar = int(d.get("saltar", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("Los renglones que se saltan son un número.") from None
    if not 0 <= saltar <= 1000:
        raise ValueError("Se saltan de 0 a 1000 renglones.")
    salida = {"delimitador": delim, "juntar": bool(d.get("juntar")) or delim == " ", "calificador": calif, "decimal": decimal, "columnas": list(cols),
              "saltar": saltar}
    if not importar:
        def entero(k, defecto, maximo):
            try:
                v = int(d.get(k, defecto))
            except (TypeError, ValueError):
                raise ValueError(f"Revisa los decimales ({k}).") from None
            if not 0 <= v <= maximo:
                raise ValueError(f"Los decimales van de 0 a {maximo}.")
            return v
        salida.update(encabezado=bool(d.get("encabezado")), decimales=entero("decimales", 3, 6), decimales_z=entero("decimales_z", d.get("decimales", 3), 6),
                      decimales_geo=entero("decimales_geo", 8, 10))
        del salida["saltar"]
    return salida


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


def _lineas_utiles(texto: str, saltar: int = 0) -> list[tuple[int, str]]:
    """(número de línea, texto) de los renglones con datos. Solo se quita el fin de línea: un tabulador al principio es un campo vacío."""
    salida = []
    for i, linea in enumerate(texto.splitlines(), start=1):
        if i <= saltar:
            continue
        limpia = linea.strip("\x00\r\n")
        if not limpia.strip() or limpia.lstrip().startswith(_COMENTARIO):
            continue
        salida.append((i, limpia))
    return salida


def partir(linea: str, delimitador: str, juntar: bool = False, calificador: str = '"') -> list[str]:
    """Campos de un renglón.

    - `delimitador` " " (espacios): cualquier tramo de espacios o tabuladores separa; nunca hay campos vacíos.
    - Cualquier otro texto, de uno o más caracteres, separa tal cual. Con `juntar`, los campos vacíos no cuentan (varios
      delimitadores seguidos son uno).
    - `calificador`: lo que va entre calificadores es un solo campo (puede llevar el delimitador); dos calificadores seguidos dentro
      son uno literal. Los espacios alrededor de cada campo se quitan.
    """
    if not delimitador:
        raise ValueError("Falta el delimitador.")
    espacios = delimitador == " "
    if not calificador or calificador not in linea:
        campos = linea.split() if espacios else [c.strip() for c in linea.split(delimitador)]
        return [c for c in campos if c] if juntar and not espacios else campos
    n = len(linea)
    es_delim = (lambda j: linea[j] in " \t") if espacios else (lambda j: linea.startswith(delimitador, j))
    paso = 1 if espacios else len(delimitador)
    campos: list[str] = []
    i = 0
    if espacios:
        while i < n and linea[i] in " \t":
            i += 1
    while True:
        j = i
        while j < n and linea[j] in " \t" and not es_delim(j):
            j += 1
        if linea.startswith(calificador, j):
            j += len(calificador)
            partes = []
            while j < n:
                if linea.startswith(calificador, j):
                    if linea.startswith(calificador, j + len(calificador)):
                        partes.append(calificador)
                        j += 2 * len(calificador)
                        continue
                    j += len(calificador)
                    break
                partes.append(linea[j])
                j += 1
            k = j
            while k < n and not es_delim(k):
                k += 1
            valor = "".join(partes) + linea[j:k].strip()
            citado = True
        else:
            k = j
            while k < n and not es_delim(k):
                k += 1
            valor = linea[j:k].strip()
            citado = False
        if valor or citado or not (juntar or espacios):
            campos.append(valor)
        if k >= n:
            break
        i = k + paso
        if espacios:
            while i < n and linea[i] in " \t":
                i += 1
            if i >= n:
                break
        elif i >= n:
            if not juntar:
                campos.append("")  # el delimitador al final deja un campo vacío
            break
    return campos


def detectar_separador(lineas: Sequence[str], calificador: str = '"') -> str:
    """Elige el delimitador que da el mismo número de campos (≥ 2) en más renglones; si ninguno, espacios."""
    muestra = list(lineas[:200])
    if not muestra:
        return " "
    mejor, mejor_puntos = " ", -1.0
    for sep in ("\t", ";", ",", "|"):
        cuentas = [len(partir(linea, sep, False, calificador)) for linea in muestra]
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
    return partir(linea, sep)


def _a_numero(texto: str, decimal: str) -> float:
    t = texto.strip().replace(" ", "").replace(" ", "")
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


_SIMBOLOS_DMS = re.compile(r"[\d.,\s°º˚'′’\"″:DMS]+")


def leer_angulo(texto: str, decimal: str = ".", eje: str = "lat") -> float:
    """Latitud o longitud en grados: «19.4326», «-99.1332», «99.1332 W», «19°25'57.4"N», «W 99 07 59.5», «19:25:57.4».

    S, W y O (oeste) hacen el valor negativo; un signo «-» también. Falla con un mensaje claro.
    """
    nombre = "La latitud" if eje == "lat" else "La longitud"
    t = str(texto).strip().upper()
    if not t:
        raise ValueError(f"{nombre} está vacía.")
    hemi = ""
    if t[0] in "NSEWO":
        hemi, t = t[0], t[1:].strip()
    if t and t[-1] in "NSEWO" and not hemi:
        hemi, t = t[-1], t[:-1].strip()
    if hemi and (hemi in "NS") != (eje == "lat"):
        raise ValueError(f"{nombre} no puede ser {hemi}.")
    signo = 1.0
    if t[:1] in "+-":
        signo, t = (-1.0 if t[0] == "-" else 1.0), t[1:].strip()
    if not t:
        raise ValueError(f"{nombre} no es un número.")
    try:
        if not re.fullmatch(r"[\d.,]+", t):
            raise ValueError
        valor = _a_numero(t, decimal)
    except ValueError:
        if not _SIMBOLOS_DMS.fullmatch(t):
            raise ValueError(f"{nombre} «{texto}» no es un ángulo.") from None
        partes = re.findall(r"\d+(?:[.,]\d+)?", t)
        if not 1 <= len(partes) <= 3:
            raise ValueError(f"{nombre} «{texto}» no es un ángulo.") from None
        g, m, s = [float(p.replace(",", ".")) for p in partes] + [0.0] * (3 - len(partes))
        if m >= 60 or s >= 60 or (len(partes) > 1 and not float(g).is_integer()):
            raise ValueError(f"{nombre} «{texto}» tiene minutos o segundos de más.") from None
        valor = g + m / 60.0 + s / 3600.0
    if hemi in ("S", "W", "O"):
        valor = -abs(valor)
    valor *= signo
    if abs(valor) > (90.0 if eje == "lat" else 180.0):
        raise ValueError(f"{nombre} «{texto}» está fuera de rango.")
    return valor


def _es_angulo(texto: str, decimal: str) -> bool:
    try:
        leer_angulo(texto, decimal, "lon")
        return True
    except ValueError:
        return False


def _es_valor(texto: str, decimal: str) -> bool:
    return _es_numero(texto, decimal) or (bool(re.search(r"\d", texto)) and _es_angulo(texto, decimal))


_NOMBRES = {
    "P": ("p", "pt", "pto", "punto", "puntos", "no", "num", "numero", "número", "nombre", "name", "point", "id", "est", "estacion", "estación"),
    "N": ("n", "y", "norte", "north", "northing", "coordy", "coordenaday"),
    "E": ("e", "x", "este", "east", "easting", "coordx", "coordenadax"),
    "Z": ("z", "elev", "elevacion", "elevación", "elevation", "cota", "altura", "h", "alt", "altitud", "altitude"),
    "D": ("d", "desc", "descripcion", "descripción", "description", "code", "codigo", "código", "clave", "nota"),
    "LAT": ("lat", "latitud", "latitude", "phi"),
    "LON": ("lon", "long", "lng", "longitud", "longitude", "lambda"),
}


def _columnas_de_encabezado(campos: Sequence[str]) -> list[str] | None:
    """Columnas por los nombres del encabezado; lo que no se reconoce no se lee («-»). None si no alcanza para leer coordenadas."""
    cols = []
    for campo in campos:
        llave = re.sub(r"[^a-záéíóúñ]", "", campo.lower())
        letra = next((k for k, nombres in _NOMBRES.items() if llave in nombres), "-")
        if letra != "-" and letra in cols:
            return None
        cols.append(letra)
    try:
        return normalizar_columnas(cols)
    except ValueError:
        return None


def _decimales_de(texto: str) -> int:
    m = re.search(r"[.,](\d+)$", texto.strip())
    return len(m.group(1)) if m else 0


def sugerir_columnas(filas: Sequence[Sequence[str]], decimal: str) -> tuple[list[str] | None, str]:
    """Sugiere las columnas por el contenido: coordenadas UTM de México o geográficas. Devuelve (columnas, motivo) o (None, "")."""
    filas = [f for f in filas if len(f) >= 2]
    if not filas:
        return None, ""
    n = min(len(f) for f in filas)
    numericas = [all(_es_numero(f[i], decimal) for f in filas) for i in range(n)]
    angulos = [all(_es_valor(f[i], decimal) and _es_angulo(f[i], decimal) for f in filas) for i in range(n)]
    tiene_p = not (numericas[0] or angulos[0]) or (n >= 4 and all(numericas[:4]) and _parece_numeracion(filas))
    inicio = 1 if tiene_p else 0
    coords = [i for i in range(inicio, n) if numericas[i] or angulos[i]]
    if len(coords) < 2:
        return None, ""
    a, b = coords[0], coords[1]
    motivo = ""
    if numericas[a] and numericas[b]:
        va = [_a_numero(f[a], decimal) for f in filas]
        vb = [_a_numero(f[b], decimal) for f in filas]
        # UTM en México: este entre 160 000 y 840 000 m, norte entre 1 500 000 y 3 700 000 m.
        es_e = lambda v: 100_000 <= v <= 900_000
        es_n = lambda v: 1_000_000 <= v <= 10_000_000
        if all(map(es_e, va)) and all(map(es_n, vb)):
            par, motivo = ["E", "N"], "utm"
        elif all(map(es_n, va)) and all(map(es_e, vb)):
            par, motivo = ["N", "E"], "utm"
    if not motivo and angulos[a] and angulos[b]:
        # geográficas: grados con muchas cifras decimales (o en grados, minutos y segundos); unas coordenadas locales pequeñas no lo son
        dms = any(not _es_numero(f[i], decimal) for f in filas for i in (a, b))
        finos = all(sorted(_decimales_de(f[i]) for f in filas)[len(filas) // 2] >= 5 for i in (a, b))
        if dms or finos:
            va = [abs(leer_angulo(f[a], decimal, "lon")) for f in filas]
            vb = [abs(leer_angulo(f[b], decimal, "lon")) for f in filas]
            if all(v <= 90 for v in va):
                par, motivo = ["LAT", "LON"], "geograficas"
            elif all(v <= 90 for v in vb):
                par, motivo = ["LON", "LAT"], "geograficas"
    if not motivo:
        return None, ""
    # lo que hay entre la primera columna y las coordenadas, o entre ellas, no se lee (la persona lo puede asignar)
    cols = (["P"] if tiene_p else []) + ["-"] * (a - inicio) + [par[0]] + ["-"] * (b - a - 1) + [par[1]]
    resto = list(range(b + 1, n))
    if resto and numericas[resto[0]]:
        cols.append("Z")
        resto = resto[1:]
    if resto:
        cols.append("D")
    return normalizar_columnas(cols), motivo


def sugerir_orden(filas: Sequence[Sequence[str]], decimal: str) -> tuple[str | None, str]:
    """Como `sugerir_columnas`, con el resultado en letras («PNEZD») o separado por comas."""
    cols, motivo = sugerir_columnas(filas, decimal)
    return (_orden_texto(cols) if cols else None), motivo


def _parece_numeracion(filas) -> bool:
    try:
        valores = [float(f[0]) for f in filas]
    except ValueError:
        return False
    return all(v.is_integer() and 0 < v < 1e7 for v in valores)


def _decimal_de(filas, sep: str) -> str:
    if "," in sep:
        return "."
    muestra = [c for _, _, f in filas[:50] for c in f]
    if any(re.fullmatch(r"-?\d+,\d+", c) for c in muestra) and not any(re.fullmatch(r"-?\d+\.\d+", c) for c in muestra):
        return ","
    return "."


def leer_puntos(
    datos: bytes | str,
    orden: str | None = None,
    separador: str | None = None,
    decimal: str | None = None,
    tipo: str = TERRENO,
    prefijo_numero: str = "",
    *,
    columnas: Sequence[str] | None = None,
    juntar: bool = False,
    calificador: str = '"',
    saltar: int = 0,
    numero_libre: int | None = None,
    zona: int | None = None,
    hemisferio: str = "N",
    muestra: int = 8,
) -> Lectura:
    """Lee un archivo de puntos.

    `orden` (letras) o `columnas` (lista) dicen qué es cada campo; sin ninguno, se toman del encabezado o se sugieren por el
    contenido y, si no se puede, falla con un mensaje que pide elegirlas. `separador`/`decimal` None: se reconocen. `saltar`: líneas
    del principio que no se leen. Los renglones con coordenadas no válidas se descartan con su motivo. Un punto sin número toma el
    siguiente número libre: desde `numero_libre` (el siguiente del plano) o el siguiente al mayor número del archivo. Con latitud y
    longitud, `zona` y `hemisferio` son los del plano: las coordenadas se convierten a esa zona UTM.
    """
    if isinstance(datos, bytes):
        texto, codificacion = decodificar(datos)
    else:
        texto, codificacion = datos, "texto"
    lineas = _lineas_utiles(texto, max(0, int(saltar or 0)))
    if not lineas:
        raise ValueError("El archivo no tiene renglones con datos." if not saltar else "Después de los renglones que se saltan no quedan datos.")
    calificador = calificador or ""
    sep = separador if separador else detectar_separador([t for _, t in lineas], calificador)
    juntar = bool(juntar) or sep == " "
    filas = [(i, t, partir(t, sep, juntar, calificador)) for i, t in lineas]
    if not decimal:
        decimal = _decimal_de(filas, sep)
    encabezado = None
    cols_enc = None
    primera = filas[0][2]
    if sum(_es_valor(c, decimal) for c in primera) < 2:
        encabezado = primera
        cols_enc = _columnas_de_encabezado(primera)
        filas = filas[1:]
    if not filas:
        raise ValueError("El archivo solo tiene el encabezado.")
    confianza = ""
    sugeridas = None
    if columnas:
        cols = normalizar_columnas(columnas)
    elif orden:
        cols = list(normalizar_orden(orden))
    elif cols_enc:
        cols, confianza = cols_enc, "encabezado"
    else:
        sugeridas, confianza = sugerir_columnas([f for _, _, f in filas[:200]], decimal)
        if sugeridas is None:
            raise ValueError("No se pudo reconocer el orden de las columnas: elige qué es cada una (por ejemplo PNEZD o PENZD).")
        cols = sugeridas
    geo = "LAT" in cols
    if geo and zona is None:
        raise ValueError("Elige la zona UTM del plano para convertir la latitud y la longitud.")
    texto_orden = _orden_texto(cols)
    lectura = Lectura([], texto_orden, sep, decimal, codificacion, encabezado, [], _orden_texto(sugeridas or cols_enc) if (sugeridas or cols_enc) else None,
                      confianza, list(cols), juntar, calificador, int(saltar or 0), max((len(f) for _, _, f in filas[:200]), default=0),
                      [(i, f) for i, _, f in filas[:muestra]], geo)
    minimo = len(cols) - (1 if cols[-1] == "D" else 0)
    nombre_formato = f"el orden {texto_orden}" if all(len(c) == 1 and c != "-" for c in cols) else "el formato"
    pos = {c: k for k, c in enumerate(cols) if c != "-"}
    if geo:
        from .geodesia import geo_a_utm, zona_de_longitud
    # siguiente número libre: después del que da el plano y del mayor número del archivo
    mayor = 0
    if "P" in pos:
        for _, _, f in filas:
            v = f[pos["P"]].strip() if pos["P"] < len(f) else ""
            if v.isdigit() and len(v) < 10:
                mayor = max(mayor, int(v))
    siguiente = max(int(numero_libre or 1), mayor + 1)
    une = " " if sep == " " else sep
    for linea, original, campos in filas:
        if len(campos) < minimo:
            lectura.descartados.append((linea, original, f"tiene {len(campos)} campos y {nombre_formato} pide {minimo}"))
            continue
        valores = {c: campos[k] for c, k in pos.items() if k < len(campos)}
        if cols[-1] == "D" and len(campos) > len(cols):
            valores["D"] = une.join(campos[len(cols) - 1:]).strip()
        try:
            if geo:
                try:
                    lat = leer_angulo(valores["LAT"], decimal, "lat")
                except ValueError as exc:
                    lectura.descartados.append((linea, original, str(exc)))
                    continue
                try:
                    lon = leer_angulo(valores["LON"], decimal, "lon")
                except ValueError as exc:
                    lectura.descartados.append((linea, original, str(exc)))
                    continue
                x, y = geo_a_utm(lat, lon, int(zona), hemisferio)
                z_lon = zona_de_longitud(lon)
                lectura.zonas[z_lon] = lectura.zonas.get(z_lon, 0) + 1
                if (lat < 0) != (hemisferio == "S"):
                    lectura.zonas.setdefault(-1, 0)
                    lectura.zonas[-1] += 1
            else:
                x = _a_numero(valores["E"], decimal)
                y = _a_numero(valores["N"], decimal)
            z = _a_numero(valores["Z"], decimal) if "Z" in valores and valores["Z"] not in ("", SIN_ELEVACION) else None
        except ValueError:
            lectura.descartados.append((linea, original, "coordenada no numérica"))
            continue
        numero = valores.get("P", "").strip()
        if not numero:
            numero = str(siguiente)
            siguiente += 1
            lectura.sin_numero += 1
        lectura.puntos.append(Punto(prefijo_numero + numero, x, y, z, valores.get("D", "").strip(), tipo))
    if not lectura.puntos:
        raise ValueError(f"Ningún renglón tiene coordenadas válidas con {nombre_formato}.")
    if geo:
        fuera_hemi = lectura.zonas.pop(-1, 0)
        otras = {k: v for k, v in lectura.zonas.items() if k != int(zona)}
        for k, v in sorted(otras.items(), key=lambda kv: -kv[1]):
            lectura.avisos.append(f"{v} punto{'s' if v != 1 else ''} cae{'n' if v != 1 else ''} en la zona {k}; se convirtieron a la zona {zona} del plano."
                                  + (" ¿Les falta el signo de la longitud (oeste)?" if k > 30 and zona <= 30 else ""))
        if fuera_hemi:
            lectura.avisos.append(f"{fuera_hemi} punto{'s' if fuera_hemi != 1 else ''} está{'n' if fuera_hemi != 1 else ''} en el otro hemisferio.")
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
    *,
    columnas: Sequence[str] | None = None,
    calificador: str = '"',
    zona: int | None = None,
    hemisferio: str = "N",
    decimales_geo: int = 8,
) -> bytes:
    """Escribe un archivo de puntos con cualquier delimitador y columnas en cualquier orden (también latitud y longitud).

    Un texto que contiene el delimitador va entre calificadores (sin calificador, el delimitador del texto se cambia por «_» o por un
    espacio). El punto sin elevación escribe el campo Z vacío («-» si el delimitador es espacio, para no correr las columnas). Con
    espacios, la descripción al final del renglón puede llevar espacios. «-» escribe un campo vacío.
    """
    cols = normalizar_columnas(columnas) if columnas else list(normalizar_orden(orden))
    if not separador:
        raise ValueError("Elige el delimitador.")
    if decimal == "," and "," in separador:
        raise ValueError("Con coma decimal el separador no puede ser coma.")
    if ("LAT" in cols or "LON" in cols) and zona is None:
        raise ValueError("Elige la zona UTM del plano para escribir latitud y longitud.")
    if "LAT" in cols:
        from .geodesia import datos_geo
    dz = decimales if decimales_z is None else decimales_z
    salida = io.StringIO()
    if encabezado:
        salida.write(separador.join(_campo_texto(NOMBRES_COLUMNA.get(c, ""), separador, calificador) for c in cols) + fin_de_linea)
    for p in puntos:
        campos = []
        geo = datos_geo(p.x, p.y, int(zona), hemisferio) if "LAT" in cols else None
        for k, letra in enumerate(cols):
            if letra == "P":
                campos.append(_campo_texto(p.numero, separador, calificador))
            elif letra == "N":
                campos.append(_fmt(p.y, decimales, decimal))
            elif letra == "E":
                campos.append(_fmt(p.x, decimales, decimal))
            elif letra == "Z":
                campos.append(SIN_ELEVACION if p.z is None and separador == " " else _fmt(p.z, dz, decimal))
            elif letra == "LAT":
                campos.append(_fmt(geo.latitud, decimales_geo, decimal))
            elif letra == "LON":
                campos.append(_fmt(geo.longitud, decimales_geo, decimal))
            elif letra == "D":
                campos.append(p.descripcion if separador == " " and k == len(cols) - 1 else _campo_texto(p.descripcion, separador, calificador))
            else:
                campos.append(SIN_ELEVACION if separador == " " else "")
        renglon = separador.join(campos)
        salida.write((renglon.rstrip() if separador == " " else renglon) + fin_de_linea)
    return salida.getvalue().encode(codificacion)


def _campo_texto(texto: str, separador: str, calificador: str = '"') -> str:
    if separador == " ":
        return texto.replace(" ", "_") if " " in texto else texto
    if calificador and (separador in texto or calificador in texto):
        return calificador + texto.replace(calificador, calificador * 2) + calificador
    if separador in texto:
        return texto.replace(separador, "_" if separador.strip() == "" else " ")
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
