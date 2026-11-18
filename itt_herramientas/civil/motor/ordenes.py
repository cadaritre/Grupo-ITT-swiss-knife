"""Órdenes de la extensión Civil: entrada y salida en JSON, para el servidor de Herramientas.

Cada orden recibe un diccionario (lo que la página leyó del dibujo y lo que la persona eligió en la
hoja) y devuelve un diccionario con:
- `resumen`: textos y cifras para mostrar (en español llano);
- `avisos`: lista de frases;
- `dxf`: el DXF a insertar en el plano, en base64 (si la orden dibuja);
- datos propios de la orden (`caras`, `borrar`, `archivo`…).

Los errores de la persona salen como `ErrorOrden` con un mensaje para mostrar tal cual.
Formatos de entrada comunes:
- punto: `[x, y, z, "número", "descripción"]` (z puede ser null);
- cara: `{"handle": "7E", "vertices": [[x, y, z], [x, y, z], [x, y, z]]}`;
- polilínea: `{"vertices": [[x, y(, z)], …], "combas": [b, …], "cerrada": bool}`.
"""

from __future__ import annotations

import base64
import json
import math

import numpy as np
from typing import Any, Callable

from . import dxf as dxf_mod
from .cuadro import FORMATOS as FORMATOS_CUADRO
from .cuadro import OpcionesCuadro, calcular_cuadro, dibujar_cuadro, resumen_geo
from .cuadro_curvas import curvas_de_polilineas, dibujar_cuadro_curvas
from .anotacion import CAPAS_ANOTACION, CAPAS_RETICULA, anotar_lados, cuadricula_utm, intervalo_reticula, numerar_vertices
from .curvas import CAPAS_CURVAS, Curva, ancho_etiqueta, curvas_de_nivel, dibujar_curvas, etiquetar, suavizar, texto_etiqueta, unir_cortadas
from .curvas import _en as _en_curva
from .dibujos import APP, BLOQUES, CAPAS_PUNTOS, bloque_punto, dibujar_puntos, dibujar_tin
from .edicion_tin import ErrorEdicion, agregar, invertir, normalizar_cara, insertar_puntos, quitar_dentro, quitar_fuera, quitar_largos, revisar
from .eje import Eje, EjePI, TramoEspiral, formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Insercion, Linea, Polilinea, Polilinea3D, Texto
from .escala import ALTURAS_MM, Escala
from .memoria import memoria_descriptiva, reporte_puntos
from .perfiles import PIV, Rasante, dibujar_perfil, perfil_terreno
from .puntos import PROYECTO, TERRENO, Punto, escribir_puntos, leer_puntos, normalizar_formato, normalizar_orden, numeros_repetidos
from .lotificacion import dibujar_lotes, lotificar
from .masa import compensar, dibujar_curva_masa
from .secciones import Plantilla, PlantillaCarretera, dibujar_secciones, secciones
from .superficie import formato_ha_a_ca, formato_miles
from .tin import LineaQuiebre, crear_tin
from .volumenes import CAPAS_VOLUMEN, dibujar_tabla_volumenes, volumen_entre_superficies, volumenes_por_secciones

__all__ = ["ErrorOrden", "ORDENES", "ejecutar", "PESADAS"]


class ErrorOrden(ValueError):
    """Error que la persona puede corregir; el mensaje se muestra tal cual."""


def _num(d: dict, k: str, defecto=None, minimo=None, maximo=None) -> float:
    v = d.get(k, defecto)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ErrorOrden(f"Revisa el valor de «{k}».") from None
    if not math.isfinite(v) or (minimo is not None and v < minimo) or (maximo is not None and v > maximo):
        raise ErrorOrden(f"El valor de «{k}» está fuera de rango.")
    return v


def _escala(d: dict) -> Escala:
    e = d.get("escala") or {}
    alturas = {k: float(v) for k, v in (e.get("alturas_mm") or {}).items() if k in ALTURAS_MM}
    try:
        return Escala(e.get("denominador", 1000), alturas or dict(ALTURAS_MM))
    except (ValueError, KeyError) as exc:
        raise ErrorOrden(str(exc)) from None


def _dxf(d: Dibujo) -> str:
    return base64.b64encode(dxf_mod.a_bytes(d)).decode("ascii")


def _caras(lista) -> list:
    if not lista:
        raise ErrorOrden("No hay triangulación en el dibujo. Primero triangula.")
    try:
        return [normalizar_cara(c["vertices"] if isinstance(c, dict) else c) for c in lista]
    except (ErrorEdicion, KeyError, TypeError, IndexError) as exc:
        raise ErrorOrden(f"Una cara de la triangulación no es válida: {exc}") from None


def _puntos(lista, tipo=TERRENO) -> list[Punto]:
    salida = []
    for p in lista or []:
        if len(p) < 2:
            continue
        z = None if len(p) < 3 or p[2] is None else float(p[2])
        salida.append(Punto(str(p[3]) if len(p) > 3 and p[3] is not None else "", float(p[0]), float(p[1]), z, str(p[4]) if len(p) > 4 and p[4] else "", tipo))
    return salida


def _zona(valor) -> int:
    try:
        z = int(valor)
    except (TypeError, ValueError):
        raise ErrorOrden("Elige la zona UTM.") from None
    if not 1 <= z <= 60:
        raise ErrorOrden("La zona UTM va de 1 a 60.")
    return z


def _polilinea(p: dict | None, nombre: str):
    if not p or len(p.get("vertices") or []) < 2:
        raise ErrorOrden(f"Elige {nombre} en el dibujo.")
    v = [tuple(map(float, q)) for q in p["vertices"]]
    combas = [float(b) for b in (p.get("combas") or [0.0] * len(v))]
    if len(combas) != len(v):
        combas = (combas + [0.0] * len(v))[: len(v)]
    return v, combas


# --- puntos ---------------------------------------------------------------------------------------

_NOMBRES_SEP = {",": "coma", "\t": "tabulador", ";": "punto y coma", " ": "espacios", "|": "barra"}
_SEP_DE_NOMBRE = {v: k for k, v in _NOMBRES_SEP.items()}


def _formato(d: dict, uso: str) -> dict:
    """El formato de la orden: `formato` (el de la hoja o uno guardado) o los campos sueltos de antes (orden, separador, decimal)."""
    f = d.get("formato")
    if f is None:
        sep = d.get("separador") or ""
        f = {"delimitador": _SEP_DE_NOMBRE.get(sep, sep), "decimal": d.get("decimal") or ("," if d.get("coma_decimal") else ("" if uso == "importar" else ".")),
             "columnas": list(normalizar_orden(d["orden"])) if d.get("orden") else [], "encabezado": d.get("encabezado", False),
             "decimales": d.get("decimales", 3), "decimales_z": d.get("decimales_z", d.get("decimales", 3))}
        if uso == "exportar" and not f["delimitador"]:
            f["delimitador"] = ","
        if uso == "exportar" and not f["columnas"]:
            f["columnas"] = list("PENZD")
    try:
        return normalizar_formato(f, uso)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None


def _zona_plano(d: dict) -> tuple[int | None, str]:
    e = d.get("escala") or {}
    z = e.get("zona")
    try:
        z = int(z) if z not in (None, "") else None
    except (TypeError, ValueError):
        z = None
    return (z if z and 1 <= z <= 60 else None), ("S" if e.get("hemisferio") == "S" else "N")


def _tabla_cruda(datos: bytes, f: dict) -> dict:
    """Los primeros renglones de un archivo partidos con el formato (sin interpretar), cuando la lectura falla."""
    from .puntos import _lineas_utiles, decodificar, detectar_separador, partir

    try:
        texto, _ = decodificar(datos)
        lineas = _lineas_utiles(texto, f["saltar"])
        calif = f["calificador"]
        sep = f["delimitador"] or detectar_separador([t for _, t in lineas[:200]], calif)
        juntar = f["juntar"] or sep == " "
        filas = [(n, partir(t, sep, juntar, calif)) for n, t in lineas[:12]]
    except Exception:  # noqa: BLE001 - un archivo que ni se puede partir: la hoja solo muestra el error
        return {"tabla": [], "campos": 0, "formato": f, "encabezado": None}
    return {"tabla": [{"linea": n, "campos": [c[:60] for c in campos[:40]]} for n, campos in filas], "campos": max((len(c) for _, c in filas), default=0),
            "formato": {**f, "delimitador": sep, "juntar": juntar}, "encabezado": None}


def importar_puntos(d: dict) -> dict:
    datos = base64.b64decode(d.get("archivo") or b"")
    if not datos:
        raise ErrorOrden("Elige un archivo de puntos.")
    tipo = d.get("tipo", TERRENO)
    if tipo not in (TERRENO, PROYECTO):
        raise ErrorOrden("Elige terreno o proyecto.")
    f = _formato(d, "importar")
    zona, hemisferio = _zona_plano(d)
    try:
        libre = int(d.get("numero_libre") or 1)
    except (TypeError, ValueError):
        libre = 1
    try:
        lec = leer_puntos(datos, None, f["delimitador"] or None, f["decimal"] or None, tipo, str(d.get("prefijo", ""))[:8], columnas=f["columnas"] or None,
                          juntar=f["juntar"], calificador=f["calificador"], saltar=f["saltar"], numero_libre=libre, zona=zona,
                          hemisferio=hemisferio, muestra=12)
    except ValueError as exc:
        if d.get("solo_vista"):
            # la vista previa sigue mostrando los renglones sin interpretar, para que la persona elija qué es cada columna
            return {"error": str(exc), **_tabla_cruda(datos, f)}
        raise ErrorOrden(str(exc)) from None
    pts = lec.puntos
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    sep = _NOMBRES_SEP.get(lec.separador, f"«{lec.separador}»")
    resumen = {
        "puntos": len(pts), "orden": lec.orden, "separador": sep, "coma_decimal": lec.decimal == ",", "codificacion": lec.codificacion,
        "orden_de": {"encabezado": "el encabezado", "utm": "las coordenadas", "geograficas": "las coordenadas geográficas"}.get(lec.orden_confianza, "tu elección"),
        "repetidos": numeros_repetidos(pts), "extension": [min(xs), min(ys), max(xs), max(ys)],
        "sin_elevacion": sum(1 for p in pts if p.z is None), "sin_numero": lec.sin_numero, "geograficas": lec.geograficas,
        "zonas": {str(k): v for k, v in lec.zonas.items()},
    }
    salida: dict[str, Any] = {
        "resumen": resumen,
        # el formato con el que se leyó (lo reconocido llena la hoja; lo elegido se respeta)
        "formato": {"delimitador": lec.separador, "juntar": lec.juntar, "calificador": lec.calificador, "decimal": lec.decimal, "columnas": lec.columnas,
                    "saltar": lec.saltar},
        "campos": lec.campos,
        "encabezado": lec.encabezado,
        "tabla": [{"linea": n, "campos": [c[:60] for c in campos[:40]]} for n, campos in lec.muestra],
        "muestra": [[p.numero, p.x, p.y, p.z, p.descripcion] for p in pts[:20]],
        "descartados": [{"linea": n, "texto": t[:120], "motivo": m} for n, t, m in lec.descartados[:200]],
        "avisos": ([f"{len(lec.descartados)} renglones no se leyeron."] if lec.descartados else [])
        + ([f"{len(resumen['repetidos'])} números de punto se repiten."] if resumen["repetidos"] else [])
        + ([f"{lec.sin_numero} puntos sin número toman los siguientes números libres."] if lec.sin_numero else [])
        + lec.avisos,
    }
    if not d.get("solo_vista"):
        e = d.get("etiquetas") or {}
        salida["dxf"] = _dxf(dibujar_puntos(pts, _escala(d), int(d.get("decimales_z", 3)), e.get("numero", True), e.get("elevacion", True), e.get("descripcion", True)))
    return salida


def exportar_puntos(d: dict) -> dict:
    pts = _puntos(d.get("puntos"))
    if not pts:
        raise ErrorOrden("No hay puntos para exportar.")
    f = _formato(d, "exportar")
    zona, hemisferio = _zona_plano(d)
    try:
        datos = escribir_puntos(pts, separador=f["delimitador"], decimales=f["decimales"], decimales_z=f["decimales_z"], decimal=f["decimal"],
                                encabezado=f["encabezado"], fin_de_linea="\r\n", columnas=f["columnas"], calificador=f["calificador"], zona=zona,
                                hemisferio=hemisferio, decimales_geo=f["decimales_geo"])
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"archivo": base64.b64encode(datos).decode("ascii"), "resumen": {"puntos": len(pts), "columnas": f["columnas"]}, "avisos": []}


# --- KML / KMZ -------------------------------------------------------------------------------------

def _reservadas() -> set[str]:
    from .curvas import CAPAS_CURVAS
    from .dibujos import CAPAS_TIN

    return {c.nombre for c in CAPAS_PUNTOS.values()} | {c.nombre for c in CAPAS_TIN.values()} | {c.nombre for c in CAPAS_CURVAS.values()}


def importar_kml(d: dict) -> dict:
    """KML o KMZ al plano. Con `solo_vista`, solo las cuentas (la vista previa antes de insertar)."""
    from .kml import dibujar_kml, leer_kml

    datos = base64.b64decode(d.get("archivo") or b"")
    if not datos:
        raise ErrorOrden("Elige un archivo KML o KMZ.")
    zona, hemisferio = _zona_plano(d)
    if zona is None:
        raise ErrorOrden("Elige la zona UTM del plano (botón de la escala).")
    try:
        lec = leer_kml(datos)
        if not lec.marcas:
            raise ValueError("El archivo no trae polígonos, líneas ni puntos.")
        r = dibujar_kml(lec, zona, hemisferio, _escala(d), d.get("capas_por", "carpeta"), d.get("puntos_como", "terreno"), bool(d.get("nombres", True)),
                        bool(d.get("altitudes", True)), int(_num(d, "numero_libre", 1, minimo=1)), _reservadas())
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    carpetas = sorted({" / ".join(m.carpeta) for m in lec.marcas if m.carpeta})
    con_altitud = sum(1 for m in lec.marcas for g in m.geometrias for a in g.anillos for p in a if p[2] not in (None, 0.0))
    resumen = {**r.cuentas, "capas": r.capas, "carpetas": carpetas[:100], "estilos": len({m.estilo for m in lec.marcas if m.estilo}), "nombre": lec.nombre,
               "zonas": {str(k): v for k, v in r.zonas.items()}, "con_altitud": con_altitud > 0, "kmz": datos[:4] == b"PK\x03\x04"}
    salida: dict[str, Any] = {"resumen": resumen, "avisos": r.avisos}
    if not d.get("solo_vista"):
        dib = r.dibujo
        if r.puntos_civil:
            e = d.get("etiquetas") or {}
            dib.unir(dibujar_puntos(r.puntos_civil, _escala(d), int(d.get("decimales_z", 3)), e.get("numero", True), e.get("elevacion", True), e.get("descripcion", True)))
        salida["dxf"] = _dxf(dib)
    return salida


def _limpiar_texto(t: str) -> str:
    """El texto de un TEXT o MTEXT sin los códigos de formato del MTEXT."""
    import re

    t = str(t or "").replace("\\P", " ").replace("\\~", " ")
    t = re.sub(r"\\[A-Za-z][^;\\{}]*;", "", t)
    t = re.sub(r"\\[LlOoKk]", "", t).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", t).strip()


def exportar_kml(d: dict) -> dict:
    """Polilíneas cerradas (polígonos), abiertas y puntos del plano a KML o KMZ en WGS84, con nombre y superficie."""
    import shapely

    from .kml import ElementoExportar, densificar, descripcion_superficie, escribir_kml, superficie

    zona, hemisferio = _zona_plano(d)
    if zona is None:
        raise ErrorOrden("Elige la zona UTM del plano (botón de la escala).")
    polis = [p for p in (d.get("polilineas") or []) if isinstance(p, dict) and len(p.get("vertices") or []) >= 2]
    pts = _puntos(d.get("puntos"))
    if not polis and not pts:
        raise ErrorOrden("Elige polilíneas o puntos para exportar.")
    if len(polis) > 20_000 or len(pts) > 100_000:
        raise ErrorOrden("Son demasiados elementos para un KML.")
    n = d.get("nombres") or {}
    modo = n.get("modo", "etiqueta")
    if modo not in ("etiqueta", "capa", "prefijo"):
        raise ErrorOrden("Elige de dónde sale el nombre.")
    prefijo = str(n.get("prefijo", "") or "")[:40]
    inicio = int(_num(n, "inicio", 1, minimo=0, maximo=1_000_000))
    elementos: list[ElementoExportar] = []
    cerrados: list[tuple[int, Any]] = []
    for k, p in enumerate(polis):
        v, combas = _polilinea(p, "la polilínea")
        cerrada = bool(p.get("cerrada")) or (len(v) > 3 and abs(v[0][0] - v[-1][0]) < 1e-9 and abs(v[0][1] - v[-1][1]) < 1e-9)
        if cerrada and len(v) > 3 and abs(v[0][0] - v[-1][0]) < 1e-9 and abs(v[0][1] - v[-1][1]) < 1e-9:
            v, combas = v[:-1], combas[:-1]
        if cerrada and len(v) < 3:
            cerrada = False
        e = ElementoExportar("poligono" if cerrada else "linea", v, combas, capa=str(p.get("capa") or "")[:120])
        if cerrada:
            e.superficie = superficie(v, combas)
            e.descripcion = descripcion_superficie(e.superficie) + (f"<br>Capa: {e.capa}" if e.capa else "")
            try:
                g = shapely.Polygon(densificar(v, combas, True))
                if not g.is_valid:
                    g = g.buffer(0)
                cerrados.append((len(elementos), g))
            except (ValueError, shapely.errors.GEOSException):
                pass
        elementos.append(e)
    # nombres: el texto más grande dentro de cada polígono (de lo que quede dentro de varios, el polígono más chico se lo queda)
    nombrados = 0
    if modo == "etiqueta" and cerrados:
        textos = []
        for t in d.get("textos") or []:
            try:
                valor = _limpiar_texto(t.get("valor"))
                if valor:
                    textos.append((valor, float(t["posicion"][0]), float(t["posicion"][1]), float(t.get("altura") or 0)))
            except (KeyError, TypeError, ValueError, IndexError, AttributeError):
                continue
        if textos:
            arbol = shapely.STRtree([g for _, g in cerrados])
            puntos_t = shapely.points([(x, y) for _, x, y, _ in textos])
            ti, gi = arbol.query(puntos_t, predicate="within")
            dueno: dict[int, int] = {}
            for a, b in zip(ti.tolist(), gi.tolist()):
                if a not in dueno or cerrados[b][1].area < cerrados[dueno[a]][1].area:
                    dueno[a] = b
            por_poligono: dict[int, list] = {}
            for a, b in dueno.items():
                por_poligono.setdefault(b, []).append(textos[a])
            for b, lista in por_poligono.items():
                idx, g = cerrados[b]
                c = g.representative_point()
                mejor = min(lista, key=lambda t: (-round(t[3], 6), t[0].replace(".", "").replace(",", "").isdigit(), (t[1] - c.x) ** 2 + (t[2] - c.y) ** 2))
                elementos[idx].nombre = mejor[0][:120]
                nombrados += 1
    cuenta = {"poligono": 0, "linea": 0}
    por_capa: dict[str, int] = {}
    total_capa: dict[tuple[str, str], int] = {}
    for e in elementos:
        total_capa[(e.tipo, e.capa)] = total_capa.get((e.tipo, e.capa), 0) + 1
    for e in elementos:
        cuenta[e.tipo] += 1
        if e.nombre:
            continue
        if modo == "capa" and e.capa:
            por_capa[(e.tipo, e.capa)] = por_capa.get((e.tipo, e.capa), 0) + 1
            e.nombre = e.capa if total_capa[(e.tipo, e.capa)] == 1 else f"{e.capa} {por_capa[(e.tipo, e.capa)]}"
        else:
            base = prefijo if modo == "prefijo" and prefijo else ("Polígono " if e.tipo == "poligono" else "Línea ")
            e.nombre = f"{base}{inicio + cuenta[e.tipo] - 1}"
    for p in pts:
        desc = "<br>".join(t for t in (p.descripcion, f"Elevación: {p.z:.3f} m" if p.z is not None else "") if t)
        elementos.append(ElementoExportar("punto", [(p.x, p.y, p.z or 0.0)], nombre=p.numero or "Punto", descripcion=desc))
    e = d.get("estilo") or {}
    kmz = d.get("formato", "kmz") == "kmz"
    try:
        datos = escribir_kml(elementos, zona, hemisferio, str(d.get("nombre") or "Plano")[:120], str(e.get("linea_color", "#ffff00")), _num(e, "linea_ancho", 2.0),
                             _num(e, "linea_opacidad", 100, minimo=0, maximo=100) / 100.0, str(e.get("relleno_color", "#ffff00")),
                             _num(e, "relleno_opacidad", 25, minimo=0, maximo=100) / 100.0, kmz)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    sup = sum(x.superficie or 0.0 for x in elementos)
    return {"archivo": base64.b64encode(datos).decode("ascii"), "extension": "kmz" if kmz else "kml",
            "tipo": "application/vnd.google-earth.kmz" if kmz else "application/vnd.google-earth.kml+xml",
            "resumen": {"poligonos": cuenta["poligono"], "lineas": cuenta["linea"], "puntos": len(pts), "superficie": sup,
                        "con_etiqueta": nombrados}, "avisos": []}


def _orden_natural(texto: str):
    """«P2» antes que «P10»: los tramos de cifras se comparan como números."""
    import re

    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.findall(r"\d+|\D+", texto or "")]


def redibujar_puntos(d: dict) -> dict:
    """Vuelve a dibujar puntos que ya están en el plano, a la escala de `escala` y, con `renumerar`, con números nuevos.

    Cada punto: {"x", "y", "z", "tipo", "atributos": {"NUM", "ELEV", "DESC"}}. Los atributos se conservan tal como están
    (un rótulo apagado sigue vacío; la elevación, con sus decimales). `renumerar`: {"inicio", "incremento", "prefijo",
    "orden": "numero" | "oeste_este" | "norte_sur" | "como_estan"}. Devuelve el DXF; la página borra los anteriores.
    """
    lista = d.get("puntos") or []
    if not lista:
        raise ErrorOrden("No hay puntos para volver a dibujar.")
    pts = []
    for q in lista:
        try:
            a = q.get("atributos") or {}
            tipo = q.get("tipo", TERRENO)
            if tipo not in BLOQUES:
                raise ErrorOrden("Elige terreno o proyecto.")
            pts.append({"x": float(q["x"]), "y": float(q["y"]), "z": float(q.get("z") or 0.0), "tipo": tipo,
                        "NUM": str(a.get("NUM", "")), "ELEV": str(a.get("ELEV", "")), "DESC": str(a.get("DESC", ""))})
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ErrorOrden("Un punto del plano no se pudo leer.") from None
    r = d.get("renumerar")
    if r:
        orden = r.get("orden", "numero")
        claves = {"numero": lambda p: _orden_natural(p["NUM"]), "oeste_este": lambda p: (p["x"], -p["y"]), "norte_sur": lambda p: (-p["y"], p["x"])}
        if orden in claves:
            pts.sort(key=claves[orden])
        elif orden != "como_estan":
            raise ErrorOrden("Elige el orden de la numeración.")
        inicio, inc = int(_num(r, "inicio", 1, minimo=0)), int(_num(r, "incremento", 1, minimo=1, maximo=1000))
        prefijo = str(r.get("prefijo", ""))[:8]
        for k, p in enumerate(pts):
            p["NUM"] = f"{prefijo}{inicio + k * inc}"
    esc = _escala(d)
    f = float(esc.denominador) / 1000.0 * esc.unidades_por_metro
    # lo mismo que dibujar_puntos (bloque en mm de papel, inserción a E/1000, XDATA), con los atributos tal como estaban
    dib = Dibujo()
    dib.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    for k in ("numero", "elevacion", "descripcion"):
        dib.capa(CAPAS_PUNTOS[k])
    for p in pts:
        if BLOQUES[p["tipo"]] not in dib.bloques:
            dib.bloques[BLOQUES[p["tipo"]]] = bloque_punto(p["tipo"])
            dib.capa(CAPAS_PUNTOS[p["tipo"]])
        dib.agregar(Insercion(CAPAS_PUNTOS[p["tipo"]].nombre, bloque=BLOQUES[p["tipo"]], posicion=(p["x"], p["y"], p["z"]), escala=f,
                              atributos={"NUM": p["NUM"], "ELEV": p["ELEV"], "DESC": p["DESC"]},
                              datos={APP: [(1000, "punto"), (1000, p["NUM"]), (1000, p["tipo"])]}))
    return {"dxf": _dxf(dib), "resumen": {"puntos": len(pts), "renumerados": bool(r)}, "avisos": []}


# --- triangulación ---------------------------------------------------------------------------------

def _patrones(texto) -> list[str]:
    """«ARBOL, POSTE, BAR*» → patrones en mayúsculas (se aceptan * y ?)."""
    if isinstance(texto, str):
        texto = texto.replace(";", ",").split(",")
    return [t.strip().upper() for t in (texto or []) if str(t).strip()][:50]


def _excluido(p, patrones) -> bool:
    if not patrones:
        return False
    from fnmatch import fnmatchcase

    desc = (str(p[4]) if len(p) > 4 and p[4] is not None else "").strip().upper()
    return any(fnmatchcase(desc, pat) for pat in patrones)


def triangular(d: dict) -> dict:
    tipo = d.get("tipo", TERRENO)
    excluir = _patrones(d.get("excluir"))
    todos = [p for p in (d.get("puntos") or []) if len(p) >= 3 and p[2] is not None]
    pts = [(p[0], p[1], p[2]) for p in todos if not _excluido(p, excluir)]
    excluidos = len(todos) - len(pts)
    if len(pts) < 3:
        raise ErrorOrden("Se necesitan al menos tres puntos con elevación." + (f" Se dejaron fuera {excluidos} por su descripción." if excluidos else ""))
    quiebres = [LineaQuiebre([tuple(v) if len(v) > 2 else (v[0], v[1], None) for v in q["vertices"]], q.get("nombre", "")) for q in d.get("quiebres") or []]
    limite = d.get("limite", {}) and d["limite"].get("vertices")
    islas = [i["vertices"] for i in d.get("islas") or []]
    arista = d.get("arista_maxima")
    try:
        t = crear_tin(pts, quiebres, limite or None, islas, float(arista) if arista else None)
    except (ValueError, RuntimeError) as exc:
        raise ErrorOrden(str(exc)) from None
    e = t.estadisticas
    return {
        "dxf": _dxf(dibujar_tin(t, tipo)),
        "resumen": {"puntos": e.puntos_leidos - e.duplicados - e.invalidos, "triangulos": int(len(t.triangulos)), "duplicados": e.duplicados, "excluidos": excluidos,
                    "quiebres": len(quiebres), "area_planta": t.area_planta(), "area_superficie": t.area_superficie()},
        "avisos": t.avisos[:50],
    }


def invertir_triangulos(d: dict) -> dict:
    try:
        a, b = invertir(d["cara_a"]["vertices"], d["cara_b"]["vertices"])
    except ErrorEdicion as exc:
        raise ErrorOrden(str(exc)) from None
    except (KeyError, TypeError):
        raise ErrorOrden("Elige dos triángulos vecinos.") from None
    return {"cara_a": {"handle": d["cara_a"].get("handle"), "vertices": [list(p) for p in a]},
            "cara_b": {"handle": d["cara_b"].get("handle"), "vertices": [list(p) for p in b]}, "resumen": {}, "avisos": []}


def quitar_triangulos(d: dict) -> dict:
    lista = d.get("caras") or []
    caras = _caras(lista)
    modo = d.get("modo")
    try:
        if modo == "fuera":
            _, idx = quitar_fuera(caras, _polilinea(d.get("contorno"), "el polígono")[0])
        elif modo == "dentro":
            _, idx = quitar_dentro(caras, _polilinea(d.get("contorno"), "el polígono")[0])
        elif modo == "largos":
            _, idx = quitar_largos(caras, _num(d, "largo", minimo=0.001))
        else:
            raise ErrorOrden("Elige qué triángulos borrar.")
    except ErrorEdicion as exc:
        raise ErrorOrden(str(exc)) from None
    borrar = [lista[i].get("handle") for i in idx if isinstance(lista[i], dict)]
    return {"borrar": borrar, "resumen": {"borrados": len(borrar), "quedan": len(caras) - len(borrar)}, "avisos": []}


def agregar_triangulo(d: dict) -> dict:
    caras = _caras(d.get("caras")) if d.get("caras") else []
    try:
        nueva = agregar(caras, d.get("vertices") or [])
    except ErrorEdicion as exc:
        raise ErrorOrden(str(exc)) from None
    return {"dxf": _dxf(dibujar_tin([nueva], d.get("tipo", TERRENO))), "resumen": {}, "avisos": []}


#: Revisión de la triangulación: capa de las marcas (se borra y se vuelve a dibujar en cada revisión).
CAPA_REVISION = Capa("TRIANGULACION_REVISION", 1, grosor_mm=0.35, descripcion="Problemas encontrados al revisar la triangulación")


def revisar_tin(d: dict) -> dict:
    """Marca en el plano (contorno rojo) las caras con problemas y los lados con más de dos triángulos; devuelve sus handles."""
    lista = d.get("caras") or []
    if not lista:
        raise ErrorOrden("No hay triangulación en el dibujo. Primero triangula.")
    try:
        vertices = [c["vertices"] for c in lista]
    except (KeyError, TypeError):
        raise ErrorOrden("Una cara de la triangulación no es válida.") from None
    r = revisar(vertices, _num(d, "angulo_minimo", 2.0, minimo=0.0, maximo=30.0))
    dib = Dibujo()
    capa = dib.capa(CAPA_REVISION)
    for i in r.caras:
        v = vertices[i]
        try:
            pts = [(float(p[0]), float(p[1])) for p in v[:3]]
        except (TypeError, ValueError, IndexError):
            continue
        dib.agregar(Polilinea(capa, vertices=pts, cerrada=True, elevacion=0.0))
    for a, b in r.lados_de_mas:
        dib.agregar(Linea(capa, inicio=(a[0], a[1], 0.0), fin=(b[0], b[1], 0.0)))
    resumen = {"sin_superficie": len(r.sin_superficie), "repetidas": len(r.repetidas), "encimadas": len(r.encimadas),
               "alargadas": len(r.alargadas), "lados_de_mas": len(r.lados_de_mas), "total": r.total}
    return {"dxf": _dxf(dib) if dib.entidades else None, "resumen": resumen, "avisos": [],
            "handles": [lista[i].get("handle") for i in r.caras if isinstance(lista[i], dict)],
            "graves": [lista[i].get("handle") for i in sorted(set(r.sin_superficie) | set(r.repetidas) | set(r.encimadas)) if isinstance(lista[i], dict)]}


# --- curvas ----------------------------------------------------------------------------------------

def curvas(d: dict) -> dict:
    caras = _caras(d.get("caras"))
    intervalo = _num(d, "intervalo", 1.0, minimo=0.001)
    try:
        lista = curvas_de_nivel(caras, intervalo, int(d.get("maestra_cada", 5)), _num(d, "base", 0.0))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    avisos = []
    it = int(d.get("suavizado", 0))
    if it > 0:
        lista, sin = suavizar(lista, min(it, 4))
        if sin:
            avisos.append(f"{sin} curvas muy juntas se dejaron sin suavizar para que no se toquen.")
    e = d.get("etiquetas") or {}
    dib = dibujar_curvas(lista, _escala(d), _num(e, "separacion_mm", 80.0, minimo=5), int(e.get("decimales", 0)),
                         e.get("cuales", "maestras") == "maestras", bool(d.get("tres_d")),
                         sufijo="_PROYECTO" if d.get("tipo") == PROYECTO else "")
    if e.get("cuales") == "ninguna":
        dib.entidades = [x for x in dib.entidades if x.tipo != "texto_multiple"]
    return {"dxf": _dxf(dib), "resumen": {"curvas": len(lista), "maestras": sum(1 for c in lista if c.maestra),
                                         "desde": min((c.elevacion for c in lista), default=None), "hasta": max((c.elevacion for c in lista), default=None)},
            "avisos": avisos}


# --- curvas que ya están en el plano ---------------------------------------------------------------

def _curvas_del_plano(lista) -> list[tuple[dict, Curva]]:
    """Polilíneas de curvas leídas del plano: {handle, capa, vertices, elevacion, cerrada}. Maestra si su capa lo dice."""
    salida = []
    for p in lista or []:
        try:
            v = np.array([[float(q[0]), float(q[1])] for q in p.get("vertices") or []])
            z = float(p.get("elevacion") if p.get("elevacion") is not None else (p["vertices"][0][2] if len(p["vertices"][0]) > 2 else 0.0))
        except (TypeError, ValueError, KeyError, IndexError):
            raise ErrorOrden("Una curva elegida no se pudo leer.") from None
        if len(v) < 2:
            continue
        salida.append((p, Curva(z, v, bool(p.get("cerrada")), "MAESTRA" in str(p.get("capa", "")).upper())))
    if not salida:
        raise ErrorOrden("Elige curvas de nivel (polilíneas con elevación).")
    return salida


def _polilinea_curva(p: dict, c: Curva):
    datos = {"TVCIVIL": [(1000, "curva"), (1040, float(c.elevacion))]}
    capa = str(p.get("capa") or CAPAS_CURVAS["intermedias"].nombre)
    if p.get("tres_d"):
        return Polilinea3D(capa, vertices=[(float(x), float(y), float(c.elevacion)) for x, y in c.vertices], cerrada=c.cerrada, datos=datos)
    return Polilinea(capa, vertices=[(float(x), float(y)) for x, y in c.vertices], cerrada=c.cerrada, elevacion=float(c.elevacion), datos=datos)


def _tolerancia_union(d: dict, h: float) -> float:
    """El hueco más grande que se cierra: el que dejaba una etiqueta de 8 caracteres (p. ej. «1234.50») más un margen."""
    if d.get("tolerancia"):
        return _num(d, "tolerancia", minimo=0.001)
    return 1.25 * ancho_etiqueta("x" * 8, h)


def _etiquetas_del_plano(lista) -> list[dict]:
    salida = []
    for t in lista or []:
        try:
            x, y = float(t["posicion"][0]), float(t["posicion"][1])
            salida.append({"handle": t.get("handle"), "valor": str(t.get("valor") or "")[:40], "x": x, "y": y,
                           "rotacion": float(t.get("rotacion") or 0.0), "altura": float(t.get("altura") or 0.0), "capa": t.get("capa")})
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return salida


def _unir(curvas: list[tuple[dict, Curva]], tolerancia: float, etiquetas: list[dict] | None):
    """Las curvas del plano unidas donde estaban cortadas: [(propiedades de la primera pieza, curva, handles que reemplaza)], uniones.

    Con `etiquetas`, solo se cierran los huecos que tienen una etiqueta (los que dejaron las versiones anteriores); con None, cualquiera."""
    marcas = None if etiquetas is None else [(t["x"], t["y"]) for t in etiquetas]
    unidas, grupos, uniones = unir_cortadas([c for _, c in curvas], tolerancia, marcas=marcas)
    salida = []
    for c, g in zip(unidas, grupos):
        if len(g) == 1 and c is curvas[g[0]][1]:
            salida.append((curvas[g[0]][0], c, []))
        else:
            salida.append((curvas[g[0]][0], c, [curvas[k][0].get("handle") for k in g]))
    return salida, uniones


def _reponer_etiquetas(dib: Dibujo, etiquetas: list[dict], unidas: list, h: float, capa_et: str) -> list:
    """Las etiquetas viejas sobre una curva que se vuelve a dibujar se vuelven a escribir después de ella, con fondo: así tapan la curva
    nueva en vez de quedar debajo. Devuelve los handles de las que reemplazan."""
    import shapely

    lineas = [shapely.LineString(np.vstack([c.vertices, c.vertices[:1]]) if c.cerrada else c.vertices) for _, c, rem in unidas if rem]
    if not lineas or not etiquetas:
        return []
    arbol = shapely.STRtree(lineas)
    borrar = []
    for t in etiquetas:
        alto = t["altura"] or h
        pt = shapely.Point(t["x"], t["y"])
        if not any(lineas[j].distance(pt) <= 1.5 * alto for j in arbol.query(pt.buffer(1.5 * alto))):
            continue
        dib.agregar(texto_etiqueta(str(t.get("capa") or capa_et), t["valor"], t["x"], t["y"], t["rotacion"], alto))
        borrar.append(t["handle"])
    return borrar


def unir_curvas(d: dict) -> dict:
    """«Unir curvas cortadas»: las curvas que una versión anterior cortó bajo sus etiquetas vuelven a ser una sola polilínea continua.

    Se cierran los huecos con una de las `etiquetas` del plano (así cortaban las versiones anteriores); con `sin_etiquetas`, también los
    huecos sin etiqueta. Las etiquetas sobre las curvas unidas se reescriben con fondo (máscara). Con `solo_contar` solo dice cuántos
    cortes hay.
    """
    curvas = _curvas_del_plano(d.get("polilineas"))
    esc = _escala(d)
    h = esc.altura("curva_etiqueta")
    etiquetas = _etiquetas_del_plano(d.get("etiquetas"))
    unidas, uniones = _unir(curvas, _tolerancia_union(d, h), None if d.get("sin_etiquetas") else etiquetas)
    resumen = {"cortes": len(uniones), "antes": len(curvas), "despues": len(unidas)}
    if d.get("solo_contar"):
        return {"resumen": resumen, "avisos": []}
    if not uniones:
        raise ErrorOrden("Esas curvas no están cortadas: ya son continuas.")
    capa_et = str(d.get("capa_etiquetas") or CAPAS_CURVAS["etiquetas"].nombre)
    dib = Dibujo()
    dib.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    dib.capa(Capa(capa_et, CAPAS_CURVAS["etiquetas"].color, descripcion=CAPAS_CURVAS["etiquetas"].descripcion))
    borrar = []
    for p, c, rem in unidas:
        if rem:
            dib.agregar(_polilinea_curva(p, c))
            borrar += rem
    borrar += _reponer_etiquetas(dib, etiquetas, unidas, h, capa_et)
    resumen["etiquetas"] = len(borrar) - sum(len(r) for _, _, r in unidas)
    return {"dxf": _dxf(dib), "borrar": [b for b in borrar if b], "resumen": resumen, "avisos": []}


def etiquetar_curvas(d: dict) -> dict:
    """Etiquetas de elevación en curvas que ya están dibujadas. La curva nunca se corta: cada etiqueta lleva fondo (máscara).

    - `puntos` (señalados en el plano): una etiqueta en cada uno, en la curva más cercana, alineada con ella.
    - sin puntos: a lo largo de cada curva cada `separacion_mm` de papel (todas las elegidas, maestras o no).
    - `unir`: antes de etiquetar, las curvas cortadas por una versión anterior se vuelven a unir (como «Unir curvas cortadas»); las
      piezas se reemplazan (`borrar`) por la curva continua y las etiquetas viejas de los huecos se reescriben con fondo.
    """
    curvas = _curvas_del_plano(d.get("polilineas"))
    esc = _escala(d)
    h = esc.altura("curva_etiqueta")
    dec = int(d.get("decimales", 0))
    capa_et = str(d.get("capa_etiquetas") or CAPAS_CURVAS["etiquetas"].nombre)
    dib = Dibujo()
    dib.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    dib.capa(Capa(capa_et, CAPAS_CURVAS["etiquetas"].color, descripcion=CAPAS_CURVAS["etiquetas"].descripcion))
    borrar: list = []
    uniones = []
    if d.get("unir"):
        etiquetas = _etiquetas_del_plano(d.get("etiquetas"))
        unidas, uniones = _unir(curvas, _tolerancia_union(d, h), None if d.get("sin_etiquetas") else etiquetas)
        for p, c, rem in unidas:
            if rem:
                dib.agregar(_polilinea_curva(p, c))          # primero las curvas: las etiquetas van encima
                borrar += rem
        borrar += _reponer_etiquetas(dib, etiquetas, unidas, h, capa_et)
        curvas = [(p, c) for p, c, _ in unidas]
    n = 0
    puntos = d.get("puntos") or []
    if puntos:
        import shapely

        lineas = [shapely.LineString(np.vstack([c.vertices, c.vertices[:1]]) if c.cerrada else c.vertices) for _, c in curvas]
        for q in puntos:
            pt = shapely.Point(float(q[0]), float(q[1]))
            i = min(range(len(lineas)), key=lambda k: lineas[k].distance(pt))
            if lineas[i].distance(pt) > 20 * h:
                continue
            c = curvas[i][1]
            v = np.vstack([c.vertices, c.vertices[:1]]) if c.cerrada else c.vertices
            dist = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(v, axis=0).T))])
            x, y, ang = _en_curva(v, dist, float(lineas[i].project(pt)))
            dib.agregar(texto_etiqueta(capa_et, f"{c.elevacion:.{dec}f}", x, y, ang, h))
            n += 1
    else:
        sep = _num(d, "separacion_mm", 80.0, minimo=5) * float(esc.denominador) / 1000.0 * esc.unidades_por_metro
        try:
            etiquetas = etiquetar([c for _, c in curvas], sep, h, dec, solo_maestras=False)
        except ValueError as exc:
            raise ErrorOrden(str(exc)) from None
        for e in etiquetas:
            dib.agregar(texto_etiqueta(capa_et, e.texto, e.x, e.y, e.rotacion, h))
        n = len(etiquetas)
    if not n:
        raise ErrorOrden("No quedó ninguna etiqueta: señala más cerca de las curvas o elige curvas más largas.")
    return {"dxf": _dxf(dib), "borrar": [b for b in borrar if b], "resumen": {"etiquetas": n, "cortes_unidos": len(uniones)}, "avisos": []}


def suavizar_curvas(d: dict) -> dict:
    """Suaviza curvas ya dibujadas sin que toquen a otras (las elegidas entre sí y las `fijas`). Conserva capa, elevación y XDATA."""
    import shapely

    curvas = _curvas_del_plano(d.get("polilineas"))
    it = int(_num(d, "iteraciones", 2, minimo=1, maximo=4))
    lisas, _ = suavizar([c for _, c in curvas], it)
    fijas = [c for _, c in _curvas_del_plano(d["fijas"])] if d.get("fijas") else []
    geo = lambda c: shapely.LineString(np.vstack([c.vertices, c.vertices[:1]]) if c.cerrada else c.vertices)
    arbol = shapely.STRtree([geo(c) for c in fijas]) if fijas else None
    sin = 0
    for k, c in enumerate(lisas):
        if arbol is not None and len(arbol.query(geo(c), predicate="intersects")):
            lisas[k] = curvas[k][1]
        if lisas[k] is curvas[k][1]:
            sin += 1
    dib = Dibujo()
    borrar = []
    for (p, original), c in zip(curvas, lisas):
        if c is original:
            continue
        dib.agregar(_polilinea_curva(p, c))
        borrar.append(p.get("handle"))
    if not dib.entidades:
        raise ErrorOrden("Ninguna curva se pudo suavizar sin tocar a otra.")
    avisos = [f"{sin} curva{'s' if sin != 1 else ''} muy {'juntas' if sin != 1 else 'junta'} se {'dejaron' if sin != 1 else 'dejó'} igual para que no se toquen."] if sin else []
    return {"dxf": _dxf(dib), "borrar": [b for b in borrar if b], "resumen": {"suavizadas": len(borrar)}, "avisos": avisos}


# --- cuadro de construcción -----------------------------------------------------------------------

def _cuadro_de(d: dict):
    """El cuadro calculado de la orden (polígono, opciones y vértice inicial), con sus opciones."""
    v, combas = _polilinea(d.get("poligono"), "una polilínea cerrada")
    o = d.get("opciones") or {}
    op = OpcionesCuadro(
        sentido=o.get("sentido", "horario"), inicio=int(o.get("inicio", 0)), numeracion=o.get("numeracion", "numeros"),
        primer_numero=int(o.get("primer_numero", 1)), decimales_distancia=int(o.get("decimales_distancia", 3)),
        decimales_coordenadas=int(o.get("decimales_coordenadas", 3)), decimales_segundos=int(o.get("decimales_segundos", 0)),
        decimales_superficie=int(o.get("decimales_superficie", 3)), oeste=o.get("oeste", "W"), separador_miles=o.get("separador_miles", ""),
        azimut=bool(o.get("azimut")), angulos_interiores=bool(o.get("angulos_interiores")), repetir_cierre=bool(o.get("repetir_cierre", True)),
        titulo=(o.get("titulo") or "CUADRO DE CONSTRUCCIÓN")[:80],
        formato=o.get("formato", "normal"), zona=_zona(o.get("zona", 14)), hemisferio=o.get("hemisferio", "N"),
        decimales_geo=int(o.get("decimales_geo", 4)),
    )
    if op.formato not in FORMATOS_CUADRO:
        raise ErrorOrden("Elige el formato del cuadro.")
    if "vertice_inicial" in d and d["vertice_inicial"] is not None:
        x, y = map(float, d["vertice_inicial"][:2])
        n = min(range(len(v)), key=lambda i: (v[i][0] - x) ** 2 + (v[i][1] - y) ** 2)
        c0 = calcular_cuadro(v, combas, OpcionesCuadro(sentido=op.sentido))
        op.inicio = min(range(len(c0.vertices)), key=lambda i: (c0.vertices[i][0] - v[n][0]) ** 2 + (c0.vertices[i][1] - v[n][1]) ** 2)
    try:
        c = calcular_cuadro(v, combas, op)
        resumen_geo(c, op)                                  # una zona que no corresponde a las coordenadas se dice aquí
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return c, op


def cuadro(d: dict) -> dict:
    c, op = _cuadro_de(d)
    esquina = d.get("esquina")
    if not esquina:
        xs = [p[0] for p in c.vertices]
        ys = [p[1] for p in c.vertices]
        esquina = (max(xs) + 10 * _escala(d).altura("cuadro_dato"), max(ys))
    try:
        dib = dibujar_cuadro(c, (float(esquina[0]), float(esquina[1])), _escala(d), op, numerar_vertices=bool(d.get("numerar_vertices", True)))
        geo = resumen_geo(c, op)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"dxf": _dxf(dib), "resumen": {"lados": len(c.lados), "superficie": c.superficie, "superficie_ha": c.superficie_ha,
                                         "perimetro": c.perimetro, "sentido": c.sentido, "curvas": sum(1 for l in c.lados if l.curva), **geo},
            "avisos": []}


def cuadro_curvas(d: dict) -> dict:
    """Cuadro de curvas de las polilíneas elegidas (sus lados curvos), con la clave de cada curva rotulada en el plano."""
    polis = d.get("polilineas") or []
    if not polis:
        raise ErrorOrden("Elige las polilíneas con curvas.")
    o = d.get("opciones") or {}
    try:
        lista = curvas_de_polilineas(polis, int(o.get("primer_numero", 1)), str(o.get("prefijo", "C"))[:4] or "C")
    except (KeyError, TypeError, ValueError, IndexError):
        raise ErrorOrden("Una polilínea elegida no se pudo leer.") from None
    if not lista:
        raise ErrorOrden("Lo que elegiste no tiene lados curvos.")
    esc = _escala(d)
    esquina = d.get("esquina")
    if not esquina:
        xs = [p[0] for c in lista for p in (c.inicio, c.fin, c.medio)]
        ys = [p[1] for c in lista for p in (c.inicio, c.fin, c.medio)]
        esquina = (max(xs) + 10 * esc.altura("cuadro_dato"), max(ys))
    try:
        dib = dibujar_cuadro_curvas(lista, (float(esquina[0]), float(esquina[1])), esc, (o.get("titulo") or "CUADRO DE CURVAS")[:80], bool(d.get("rotular", True)),
                                    decimales=int(o.get("decimales_distancia", 3)), decimales_segundos=int(o.get("decimales_segundos", 0)),
                                    decimales_coordenadas=int(o.get("decimales_coordenadas", 3)), oeste=o.get("oeste", "W"),
                                    separador_miles=o.get("separador_miles", ""))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"dxf": _dxf(dib), "resumen": {"curvas": len(lista), "longitud": sum(c.longitud for c in lista)}, "avisos": []}


# --- perfiles, secciones y volúmenes -------------------------------------------------------------

def _eje(d: dict) -> Eje:
    e = d.get("eje") or {}
    try:
        if isinstance(e, dict) and e.get("pi"):              # eje con espirales: sus PI y, en cada PI interior, radio y espiral
            return EjePI(e["pi"], e.get("curvas") or [], _num(d, "inicio", float(e.get("inicio", 0.0))))
        v, combas = _polilinea(e, "el eje")
        return Eje(v, combas, _num(d, "inicio", 0.0))
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ErrorOrden(str(exc) if isinstance(exc, ValueError) else "Revisa los datos del eje.") from None


def _tin_de_caras(caras):
    import numpy as np

    from .curvas import topologia_de_caras
    from .tin import EstadisticasTin, Tin

    p, t = topologia_de_caras(caras)
    return Tin(p, t, set(), EstadisticasTin())


def _rasante(d: dict) -> Rasante | None:
    r = d.get("rasante")
    if not r:
        return None
    try:
        return Rasante([PIV(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0) for p in r])
    except (ValueError, TypeError, IndexError) as exc:
        raise ErrorOrden(f"Revisa la rasante: {exc}") from None


def _tin_proyecto(d: dict):
    """La superficie de proyecto (su triangulación), si la orden la trae (`caras_proyecto`)."""
    caras = d.get("caras_proyecto")
    if not caras:
        return None
    try:
        return _tin_de_caras([normalizar_cara(c["vertices"] if isinstance(c, dict) else c) for c in caras])
    except (ErrorEdicion, KeyError, TypeError, IndexError) as exc:
        raise ErrorOrden(f"Una cara de la triangulación de proyecto no es válida: {exc}") from None


def perfil(d: dict) -> dict:
    tin = _tin_de_caras(_caras(d.get("caras")))
    eje = _eje(d)
    intervalo = _num(d, "intervalo", 20.0, minimo=0.1)
    pf = perfil_terreno(tin, eje, intervalo)
    if not any(p.z is not None for p in pf.puntos):
        raise ErrorOrden("El eje no pasa por la triangulación.")
    tp = _tin_proyecto(d)
    pf_p = perfil_terreno(tp, eje, intervalo) if tp is not None else None
    if pf_p is not None and not any(p.z is not None for p in pf_p.puntos):
        raise ErrorOrden("El eje no pasa por la triangulación de proyecto.")
    origen = d.get("origen")
    if not origen:
        x, y, _ = eje.punto(eje.inicio)
        origen = (min(p.x for p in pf.puntos), min(p.y for p in pf.puntos) - 40 * _escala(d).altura("cuadro_dato"))
    ras = _rasante(d)
    dib = dibujar_perfil(pf, (float(origen[0]), float(origen[1])), _escala(d), _num(d, "exageracion", 10.0, minimo=0.1, maximo=100), ras,
                         marcar_en_planta=False, proyecto=pf_p)
    _colocar(dib, d.get("esquina_superior"))
    if d.get("marcar_en_planta", True):
        # las marcas de estación van sobre el eje en planta: se dibujan aparte para no moverlas con el perfil
        from .perfiles import CAPAS_PERFIL
        marcas = dibujar_perfil(pf, (0.0, 0.0), _escala(d), 1.0, None, marcar_en_planta=True)
        marcas.entidades = [e for e in marcas.entidades if e.capa == CAPAS_PERFIL["estaciones"].nombre]
        dib.unir(marcas)
    fuera = sum(1 for p in pf.redondas if p.z is None)
    return {"dxf": _dxf(dib), "resumen": {"longitud": eje.longitud, "desde": formato_estacion(eje.inicio), "hasta": formato_estacion(eje.fin),
                                         "estaciones": len(pf.redondas), "puntos": len(pf.puntos)},
            "estaciones": [[p.estacion, p.z] + ([pf_p.elevacion(p.estacion)] if pf_p is not None else []) for p in pf.redondas],
            "avisos": ([f"{fuera} estaciones quedan fuera de la triangulación."] if fuera else [])
            + ([f"{fuera_p} estaciones quedan fuera de la triangulación de proyecto."] if (fuera_p := sum(1 for p in pf.redondas if pf_p is not None and pf_p.elevacion(p.estacion) is None)) else [])}


def _colocar(dib: Dibujo, esquina) -> None:
    """Con `esquina_superior` [x, y], mueve el dibujo para que su esquina superior izquierda quede ahí (ubicación automática de la página,
    a un lado de lo que ya hay en el plano)."""
    if not esquina:
        return
    caja = dib.caja()
    if caja:
        dib.trasladar(float(esquina[0]) - caja[0], float(esquina[1]) - caja[3])


def _plantilla(pl: dict):
    """La plantilla de la sección: «sencilla» (corona con bombeo) o «carretera» (carriles, acotamientos, cuneta, pavimento, despalme)."""
    try:
        if pl.get("tipo") == "carretera":
            sct = None
            c = pl.get("curvas")
            if c:
                from .sobreelevacion import ParametrosSCT, Vehiculo

                veh = c.get("vehiculo", "C3")
                if veh == "otro":
                    veh = Vehiculo("Vehículo de proyecto", _num(c, "u", 2.60, minimo=1.5, maximo=3.5), _num(c, "ev", 2.60, minimo=1.5, maximo=3.5),
                                   _num(c, "suma_li2", 66.0, minimo=0.0), _num(c, "q_vuelo", 14.0, minimo=0.0))
                sct = ParametrosSCT(_num(c, "velocidad", 60.0), _num(c, "smax", 10.0, minimo=2, maximo=12) / 100.0, veh,
                                    _num(c, "reparto_tangente", 0.6, minimo=0.5, maximo=0.7), bool(c.get("ampliar", True)))
            return PlantillaCarretera(int(_num(pl, "carriles_por_lado", 1, minimo=1, maximo=6)), _num(pl, "ancho_carril", 3.5, minimo=0.5),
                                      _num(pl, "pendiente_carril", -2.0) / 100.0, _num(pl, "acotamiento", 2.5, minimo=0),
                                      _num(pl, "pendiente_acotamiento", -4.0) / 100.0, _num(pl, "cuneta_ancho", 1.0, minimo=0),
                                      _num(pl, "cuneta_profundidad", 0.5, minimo=0), _num(pl, "espesor_pavimento", 0.30, minimo=0),
                                      _num(pl, "despalme", 0.20, minimo=0), _num(pl, "talud_corte", 1.0, minimo=0.01), _num(pl, "talud_terraplen", 1.5, minimo=0.01),
                                      sct)
        return Plantilla(_num(pl, "ancho_izq", 3.5, minimo=0), _num(pl, "ancho_der", 3.5, minimo=0), _num(pl, "bombeo", 2.0) / 100.0,
                         _num(pl, "talud_corte", 1.0, minimo=0.01), _num(pl, "talud_terraplen", 1.5, minimo=0.01))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None


def _curvas_sct(eje, plantilla) -> tuple[list[dict], list[str]]:
    """Datos de cada curva horizontal (sobreelevación, transición, ampliación) cuando la plantilla los pide."""
    if not isinstance(plantilla, PlantillaCarretera) or plantilla.sct is None:
        return [], []
    from .sobreelevacion import Peraltes

    try:
        pe = Peraltes(eje, plantilla.sct, plantilla.pendiente_carril, 2 * plantilla.carriles_por_lado, 2 * plantilla.carriles_por_lado * plantilla.ancho_carril)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return [{"pc": c.pc, "pt": c.pt, "radio": c.radio, "grado": c.grado, "lado": "izquierda" if c.izquierda else "derecha", "sobreelevacion": c.sobreelevacion,
             "transicion": c.transicion, "ampliacion": c.ampliacion} for c in pe.curvas], pe.avisos


def secciones_orden(d: dict) -> dict:
    tin = _tin_de_caras(_caras(d.get("caras")))
    eje = _eje(d)
    intervalo = _num(d, "intervalo", 20.0, minimo=0.1)
    izq, der = _num(d, "izquierda", 20.0, minimo=0.1), _num(d, "derecha", 20.0, minimo=0.1)
    ras = _rasante(d)
    tp = _tin_proyecto(d)
    if ras is not None and tp is not None:
        raise ErrorOrden("El proyecto sale de la rasante o de la triangulación de proyecto: elige una.")
    plantilla = _plantilla(d.get("plantilla") or {})
    curvas_h, avisos_h = _curvas_sct(eje, plantilla) if tp is None else ([], [])
    try:
        lista = secciones(tin, eje, eje.estaciones(intervalo), izq, der, ras, plantilla, tin_proyecto=tp)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    esc = _escala(d)
    origen = d.get("origen")
    if not origen:
        xs = [eje.punto(e)[0] for e in (eje.inicio, eje.fin)]
        ys = [eje.punto(e)[1] for e in (eje.inicio, eje.fin)]
        origen = (max(xs) + 20 * esc.altura("cuadro_dato"), max(ys))
    dib = dibujar_secciones(lista, (float(origen[0]), float(origen[1])), esc, _num(d, "exageracion", 1.0, minimo=0.1, maximo=20), int(d.get("columnas", 4)))
    resumen: dict[str, Any] = {"secciones": len(lista)}
    avisos = avisos_h + [a for s in lista for a in s.avisos][:30]
    if ras is not None or tp is not None:
        renglones = volumenes_por_secciones(lista, _num(d, "abundamiento", 1.0, minimo=0.01))
        resumen.update(corte=renglones[-1].acum_corte, terraplen=renglones[-1].acum_terraplen, ordenada_final=renglones[-1].ordenada,
                       despalme=renglones[-1].acum_despalme)
        if d.get("tabla", True):
            h = esc.altura("cuadro_dato")
            xs = [e.inicio[0] for e in dib.entidades if hasattr(e, "inicio")] or [float(origen[0])]
            ys = [e.inicio[1] for e in dib.entidades if hasattr(e, "inicio")] or [float(origen[1])]
            dib.unir(dibujar_tabla_volumenes(renglones, (float(origen[0]), min(ys) - 6 * h), esc))
    _colocar(dib, d.get("esquina_superior"))
    return {"dxf": _dxf(dib), "resumen": resumen, "curvas": curvas_h, "avisos": avisos}


def volumen_superficies(d: dict) -> dict:
    a = _caras(d.get("caras_terreno"))
    b = _caras(d.get("caras_proyecto"))
    r = volumen_entre_superficies(a, b)
    if r.area_comun <= 0:
        raise ErrorOrden("Las dos superficies no se traslapan.")
    esc = _escala(d)
    dib = Dibujo()
    if d.get("linea_cero", True):
        capa = dib.capa(CAPAS_VOLUMEN["linea_cero"])
        for p, q in r.linea_cero:
            dib.agregar(Linea(capa, inicio=(p[0], p[1], 0.0), fin=(q[0], q[1], 0.0)))
    pos = d.get("posicion")
    if pos:
        h = esc.altura("cuadro_superficie")
        tabla = dib.capa(CAPAS_VOLUMEN["tabla"])
        for k, t in enumerate([f"CORTE = {formato_miles(r.corte, 2, ',')} m³", f"TERRAPLÉN = {formato_miles(r.terraplen, 2, ',')} m³",
                               f"NETO = {formato_miles(r.neto, 2, ',')} m³", f"ÁREA COMÚN = {formato_miles(r.area_comun, 2, ',')} m²"]):
            dib.agregar(Texto(tabla, valor=t, posicion=(float(pos[0]), float(pos[1]) - k * 1.8 * h), altura=h, estilo="CIVIL"))
    return {"dxf": _dxf(dib), "resumen": {"corte": r.corte, "terraplen": r.terraplen, "neto": r.neto, "area_comun": r.area_comun}, "avisos": []}


# --- anotaciones, cuadrícula, documentos (v1.1) --------------------------------------------------

def _polis(d: dict, minimo: int = 1) -> list[dict]:
    polis = [p for p in (d.get("polilineas") or []) if isinstance(p, dict) and len(p.get("vertices") or []) >= 2]
    if len(polis) < minimo:
        raise ErrorOrden("Elige polilíneas o líneas.")
    return polis


def anotar_lados_orden(d: dict) -> dict:
    o = d.get("opciones") or {}
    try:
        dib = anotar_lados(_polis(d), _escala(d), int(o.get("decimales_distancia", 2)), int(o.get("decimales_segundos", 0)), o.get("oeste", "W"),
                           o.get("separador_miles", ""))
        if d.get("numerar_vertices"):
            dib.unir(numerar_vertices(_polis(d), _escala(d), o.get("numeracion", "numeros"), int(o.get("primer_numero", 1))))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    lados = sum(1 for e in dib.entidades if getattr(e, "capa", "") == CAPAS_ANOTACION["rumbo"].nombre)
    return {"dxf": _dxf(dib), "resumen": {"lados": lados}, "avisos": []}


def numerar_vertices_orden(d: dict) -> dict:
    o = d.get("opciones") or {}
    try:
        dib = numerar_vertices(_polis(d), _escala(d), o.get("numeracion", "numeros"), int(o.get("primer_numero", 1)))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"dxf": _dxf(dib), "resumen": {"vertices": len(dib.entidades)}, "avisos": []}


def cuadricula(d: dict) -> dict:
    caja = d.get("caja")
    if not (isinstance(caja, (list, tuple)) and len(caja) == 4):
        raise ErrorOrden("Señala la zona de la cuadrícula.")
    zona = _zona(d["zona"]) if d.get("zona") else None
    intervalo = _num(d, "intervalo", 0.0, minimo=0.0) or None
    try:
        caja = [float(v) for v in caja]
        dib = cuadricula_utm(caja, intervalo, _escala(d), d.get("tipo", "lineas"), bool(d.get("marco", True)), zona,
                             d.get("hemisferio", "N"), int(d.get("decimales_geo", 2)), d.get("oeste", "W"))
    except (TypeError, ValueError) as exc:
        raise ErrorOrden(str(exc)) from None
    textos = [e for e in dib.entidades if getattr(e, "capa", "") == CAPAS_RETICULA["textos"].nombre]
    usado = intervalo or intervalo_reticula(_escala(d), caja[2] - caja[0], caja[3] - caja[1])
    return {"dxf": _dxf(dib), "resumen": {"intervalo": usado, "textos": len(textos)}, "avisos": []}


def insertar_puntos_orden(d: dict) -> dict:
    lista = d.get("caras") or []
    caras = _caras(lista)
    pts = [p for p in (d.get("puntos") or []) if len(p) >= 3 and p[2] is not None]
    if not pts:
        raise ErrorOrden("Elige puntos con elevación.")
    try:
        r = insertar_puntos(caras, pts, bool(d.get("ajustar", True)))
    except ErrorEdicion as exc:
        raise ErrorOrden(str(exc)) from None
    avisos = []
    if r.fuera:
        avisos.append(f"{len(r.fuera)} punto{'s' if len(r.fuera) != 1 else ''} fuera de la triangulación no se {'usaron' if len(r.fuera) != 1 else 'usó'}.")
    if r.repetidos:
        avisos.append(f"{len(r.repetidos)} punto{'s' if len(r.repetidos) != 1 else ''} ya {'eran vértices' if len(r.repetidos) != 1 else 'era vértice'}.")
    if not r.nuevas:
        raise ErrorOrden("Ningún punto cae dentro de la triangulación." if r.fuera else "Esos puntos ya están en la triangulación.")
    return {"dxf": _dxf(dibujar_tin(r.nuevas, d.get("tipo", TERRENO))), "borrar": [lista[i].get("handle") for i in r.quitar if isinstance(lista[i], dict)],
            "resumen": {"insertados": len(pts) - len(r.fuera) - len(r.repetidos), "triangulos": len(r.nuevas) - len(r.quitar)}, "avisos": avisos}


def _datos_doc(d: dict) -> list[tuple[str, str]]:
    return [(str(x[0])[:60], str(x[1])[:300]) for x in (d.get("datos") or []) if isinstance(x, (list, tuple)) and len(x) == 2][:12]


def memoria(d: dict) -> dict:
    c, op = _cuadro_de(d)
    col = [str(x)[:200] for x in (d.get("colindancias") or [])]
    doc = memoria_descriptiva(c, op, col, _datos_doc(d))
    return {"documento": doc.como_dict(), "resumen": {"lados": len(c.lados), "superficie": c.superficie}, "avisos": []}


def reporte_puntos_orden(d: dict) -> dict:
    pts = _puntos(d.get("puntos"))
    if not pts:
        raise ErrorOrden("No hay puntos para el reporte.")
    zona = _zona(d["zona"]) if d.get("zona") else None
    try:
        doc = reporte_puntos([[p.x, p.y, p.z, p.numero, p.descripcion] for p in pts], int(d.get("decimales", 3)), zona, d.get("hemisferio", "N"),
                             int(d.get("decimales_geo", 4)), d.get("oeste", "W"), (d.get("titulo") or "REPORTE DE PUNTOS")[:80], _datos_doc(d))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"documento": doc.como_dict(), "resumen": {"puntos": len(pts)}, "avisos": []}


# --- después de v1.1: curva masa con compensación y lotificación ----------------------------------------------------------

def curva_masa(d: dict) -> dict:
    """Secciones del eje con su plantilla → volúmenes por áreas medias → curva masa con línea compensadora y sobreacarreo."""
    tin = _tin_de_caras(_caras(d.get("caras")))
    eje = _eje(d)
    ras = _rasante(d)
    tp = _tin_proyecto(d)
    if ras is None and tp is None:
        raise ErrorOrden("La curva masa necesita la rasante de proyecto o la triangulación de proyecto.")
    if ras is not None and tp is not None:
        raise ErrorOrden("El proyecto sale de la rasante o de la triangulación de proyecto: elige una.")
    plantilla = _plantilla(d.get("plantilla") or {})
    curvas_h, avisos_h = _curvas_sct(eje, plantilla) if tp is None else ([], [])
    try:
        lista = secciones(tin, eje, eje.estaciones(_num(d, "intervalo", 20.0, minimo=0.1)), _num(d, "izquierda", 30.0, minimo=0.1),
                          _num(d, "derecha", 30.0, minimo=0.1), ras, plantilla, tin_proyecto=tp)
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    ren = volumenes_por_secciones(lista, _num(d, "abundamiento", 1.0, minimo=0.01))
    linea = None if d.get("linea") in (None, "", "auto") else _num(d, "linea", 0.0)
    try:
        comp = compensar([r.estacion for r in ren], [r.ordenada for r in ren], linea, _num(d, "acarreo_libre", 20.0, minimo=0))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    origen = d.get("origen") or (0.0, 0.0)
    dib = dibujar_curva_masa([r.estacion for r in ren], [r.ordenada for r in ren], comp, (float(origen[0]), float(origen[1])), _escala(d),
                             _num(d, "m3_por_mm", 0.0, minimo=0.0) or None)
    _colocar(dib, d.get("esquina_superior"))
    avisos = avisos_h + [a for s_ in lista for a in s_.avisos][:20] + comp.avisos
    return {"dxf": _dxf(dib), "resumen": {"estaciones": len(ren), "corte": ren[-1].acum_corte, "terraplen": ren[-1].acum_terraplen,
                                         "despalme": ren[-1].acum_despalme, "linea": comp.linea, "puntos": len(comp.puntos), "compensado": comp.compensado,
                                         "sobreacarreo": comp.sobreacarreo, "prestamo": comp.prestamo, "desperdicio": comp.desperdicio},
            "lazos": [{"desde": l.desde, "hasta": l.hasta, "volumen": l.volumen, "distancia": l.distancia_media, "sentido": l.sentido,
                       "sobreacarreo": l.sobreacarreo} for l in comp.lazos], "curvas": curvas_h, "avisos": avisos}


def lotificar_orden(d: dict) -> dict:
    v, _ = _polilinea(d.get("poligono"), "la manzana (polilínea cerrada)")
    lado = d.get("lado_frente")
    if d.get("punto_frente") is not None:      # el lado más cercano al punto señalado
        import shapely

        q = shapely.Point(float(d["punto_frente"][0]), float(d["punto_frente"][1]))
        lado = min(range(len(v)), key=lambda i: shapely.LineString([v[i][:2], v[(i + 1) % len(v)][:2]]).distance(q))
    try:
        lotes = lotificar([p[:2] for p in v], int(lado or 0), d.get("metodo", "por_cantidad"), _num(d, "valor", 1.0), int(d.get("primer_numero", 1)),
                          str(d.get("prefijo", ""))[:8])
        tabla = d.get("esquina_tabla")
        if tabla is None and d.get("tabla", True):
            xs, ys = [p[0] for p in v], [p[1] for p in v]
            tabla = (max(xs) + 10 * _escala(d).altura("cuadro_dato"), max(ys))
        dib = dibujar_lotes(lotes, (float(tabla[0]), float(tabla[1])) if tabla else None, _escala(d), int(d.get("decimales", 3)))
    except (ValueError, TypeError) as exc:
        raise ErrorOrden(str(exc)) from None
    return {"dxf": _dxf(dib), "resumen": {"lotes": len(lotes), "superficie": sum(l.superficie for l in lotes), "lado": int(lado or 0)},
            "lotes": [{"numero": l.numero, "superficie": l.superficie, "frente": l.frente} for l in lotes], "avisos": []}


# --- eje con espirales de transición ---------------------------------------------------------------------------------------------

#: Capas del eje con espirales: el eje mismo (se elige después como eje de perfiles y secciones) y sus puntos y datos.
CAPAS_EJE = {"eje": Capa("EJE_PROYECTO", 1, grosor_mm=0.35, descripcion="Eje de proyecto con espirales"),
             "puntos": Capa("EJE_PUNTOS", 2, descripcion="TE, EC, CE y ET del eje"),
             "tabla": Capa("EJE_TABLA", 7, descripcion="Datos de las curvas del eje")}


def _definicion_xdata(definicion: dict) -> list[tuple[int, object]]:
    """La definición del eje (PI y curvas) en XDATA: cadenas de hasta 250 caracteres (el límite de DXF es 255)."""
    texto = json.dumps(definicion, separators=(",", ":"))
    return [(1000, "eje_espiral")] + [(1000, texto[i:i + 250]) for i in range(0, len(texto), 250)]


def eje_espirales(d: dict) -> dict:
    """Eje por sus PI con curvas circulares y espirales de transición (manual SCT 2018, cap. II, C.2.3 y cap. III, H.3).

    `curvas`: una por PI interior, {"radio": R, "espiral": le | "auto" | 0}; «auto» es la longitud mínima Le = 8·Vp·S con la
    sobreelevación de la curva (H.1) para `velocidad` y `smax`. Devuelve los elementos de cada curva y el dibujo: el eje (polilínea con
    arcos; las espirales en tramos de 1 m), sus TE, EC, CE y ET con su cadenamiento y la tabla de datos."""
    from .sobreelevacion import longitud_transicion, sobreelevacion_sct

    pis = [[float(p[0]), float(p[1])] for p in (d.get("pi") or [])]
    if len(pis) < 2:
        raise ErrorOrden("Elige la polilínea de las tangentes (sus vértices son los PI).")
    datos = d.get("curvas") or []
    if len(datos) != len(pis) - 2:
        raise ErrorOrden("Da el radio de cada PI.")
    v = _num(d, "velocidad", 60.0)
    smax = _num(d, "smax", 10.0, minimo=2, maximo=12) / 100.0
    curvas = []
    try:
        for c in datos:
            if not c or not c.get("radio"):
                curvas.append(None)
                continue
            r = _num(c, "radio", 0.0, minimo=1.0)
            le = c.get("espiral", "auto")
            if le == "auto":
                le = longitud_transicion(v, sobreelevacion_sct(1145.92 / r, v, smax), 2 * int(_num(d, "carriles_por_lado", 1, minimo=1, maximo=6)))
            curvas.append([r, float(le)])
        eje = EjePI(pis, curvas, _num(d, "inicio", 0.0))
    except (ValueError, TypeError) as exc:
        raise ErrorOrden(str(exc)) from None
    esc = _escala(d)
    dib = Dibujo()
    for c in CAPAS_EJE.values():
        dib.capa(c)
    dib.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    vertices, combas = [], []
    for t in eje.tramos:
        if isinstance(t, TramoEspiral):
            n = max(4, int(math.ceil(t.largo / 1.0)))
            for k in range(n):
                x, y, _ = t.punto(t.largo * k / n)
                vertices.append((x, y))
                combas.append(0.0)
        else:
            vertices.append(t.a)
            combas.append(t.comba if t.centro is not None else 0.0)
    x, y, _ = eje.punto(eje.fin)
    vertices.append((x, y))
    combas.append(0.0)
    definicion = {"pi": pis, "curvas": curvas, "inicio": eje.inicio}
    dib.agregar(Polilinea(CAPAS_EJE["eje"].nombre, vertices=vertices, combas=combas, datos={"TVCIVIL": _definicion_xdata(definicion)}))
    h = esc.altura("cuadro_dato")
    for el in eje.elementos:
        if not el:
            continue
        nombres = (("TE", "EC", "CE", "ET") if el["le"] > 0 else ("PC", "", "", "PT"))
        for nombre, clave in zip(nombres, ("te", "ec", "ce", "et")):
            if not nombre:
                continue
            x, y, direc = eje.punto(el[clave])
            nx, ny = -math.sin(direc), math.cos(direc)
            lado = -1 if el["izquierda"] else 1                   # el texto va del lado de afuera de la curva
            dib.agregar(Linea(CAPAS_EJE["puntos"].nombre, inicio=(x - nx * h, y - ny * h, 0.0), fin=(x + nx * h, y + ny * h, 0.0)))
            dib.agregar(Texto(CAPAS_EJE["puntos"].nombre, valor=f"{nombre} {formato_estacion(el[clave])}", posicion=(x + lado * nx * 2 * h, y + lado * ny * 2 * h),
                              altura=h, rotacion=math.degrees(direc) + 90 if lado > 0 else math.degrees(direc) - 90, estilo="CIVIL",
                              alineacion_h="izquierda", alineacion_v="medio"))
    filas = [el for el in eje.elementos if el]
    if filas and d.get("tabla", True):
        xs, ys = [p[0] for p in pis], [p[1] for p in pis]
        dib.unir(_tabla_eje(filas, (max(xs) + 10 * h, max(ys)), esc))
    elementos = [{k: (round(vv, 6) if isinstance(vv, float) else vv) for k, vv in el.items()} for el in filas]
    return {"dxf": _dxf(dib), "definicion": definicion, "elementos": elementos,
            "resumen": {"curvas": len(filas), "longitud": eje.longitud, "fin": eje.fin}, "avisos": []}


def _tabla_eje(filas: list[dict], esquina, esc: Escala) -> Dibujo:
    from .angulos import formato_dms

    d = Dibujo()
    capa = d.capa(CAPAS_EJE["tabla"])
    d.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    f = float(esc.denominador) / 1000.0
    h = esc.altura("cuadro_dato")
    cols = (("PI", 10), ("Δ", 22), ("Gc", 20), ("Rc", 18), ("le", 14), ("θe", 20), ("STe", 18), ("Lc", 18), ("Xc", 16), ("Yc", 14), ("k", 16),
            ("p", 12), ("TE", 22), ("EC", 22), ("CE", 22), ("ET", 22))
    xs = [esquina[0]]
    for _, a in cols:
        xs.append(xs[-1] + a * f)
    fila_h = 2 * h
    y = esquina[1]
    d.agregar(Texto(capa, valor="DATOS DE LAS CURVAS DEL EJE", posicion=((xs[0] + xs[-1]) / 2, y + fila_h * 0.6), altura=h * 1.3, estilo="CIVIL",
                    alineacion_h="centro", alineacion_v="abajo"))
    renglones = [[c for c, _ in cols]]
    for el in filas:
        renglones.append([str(el["pi"]), formato_dms(el["delta"]), formato_dms(el["grado"]), f"{el['radio']:.2f}", f"{el['le']:.2f}",
                          formato_dms(el["theta_e"]), f"{el['ste']:.3f}", f"{el['lc']:.3f}", f"{el.get('xc', 0):.3f}", f"{el.get('yc', 0):.3f}",
                          f"{el.get('k', 0):.3f}", f"{el.get('p', 0):.3f}", formato_estacion(el["te"]), formato_estacion(el["ec"]),
                          formato_estacion(el["ce"]), formato_estacion(el["et"])])
    for r in renglones:
        d.agregar(Linea(capa, inicio=(xs[0], y, 0.0), fin=(xs[-1], y, 0.0)))
        for k, t in enumerate(r):
            d.agregar(Texto(capa, valor=t, posicion=((xs[k] + xs[k + 1]) / 2, y - fila_h / 2), altura=h * 0.9, estilo="CIVIL", alineacion_h="centro", alineacion_v="medio"))
        y -= fila_h
    d.agregar(Linea(capa, inicio=(xs[0], y, 0.0), fin=(xs[-1], y, 0.0)))
    for x in xs:
        d.agregar(Linea(capa, inicio=(x, esquina[1], 0.0), fin=(x, y, 0.0)))
    return d


ORDENES: dict[str, Callable[[dict], dict]] = {
    "importar_puntos": importar_puntos,
    "exportar_puntos": exportar_puntos,
    "importar_kml": importar_kml,
    "exportar_kml": exportar_kml,
    "redibujar_puntos": redibujar_puntos,
    "triangular": triangular,
    "invertir": invertir_triangulos,
    "quitar_triangulos": quitar_triangulos,
    "agregar_triangulo": agregar_triangulo,
    "revisar_tin": revisar_tin,
    "curvas": curvas,
    "etiquetar_curvas": etiquetar_curvas,
    "unir_curvas": unir_curvas,
    "suavizar_curvas": suavizar_curvas,
    "cuadro": cuadro,
    "cuadro_curvas": cuadro_curvas,
    "anotar_lados": anotar_lados_orden,
    "numerar_vertices": numerar_vertices_orden,
    "cuadricula": cuadricula,
    "insertar_puntos": insertar_puntos_orden,
    "memoria": memoria,
    "reporte_puntos": reporte_puntos_orden,
    "perfil": perfil,
    "secciones": secciones_orden,
    "volumen_superficies": volumen_superficies,
    "curva_masa": curva_masa,
    "eje_espirales": eje_espirales,
    "lotificar": lotificar_orden,
}

#: Órdenes que pueden tardar con planos grandes: el servidor las manda a su cola de tareas.
PESADAS = {"triangular", "curvas", "perfil", "secciones", "volumen_superficies", "revisar_tin", "insertar_puntos", "curva_masa"}


def ejecutar(nombre: str, datos: dict) -> dict:
    if nombre not in ORDENES:
        raise ErrorOrden("Orden desconocida.")
    if not isinstance(datos, dict):
        raise ErrorOrden("Datos no válidos.")
    return ORDENES[nombre](datos)
