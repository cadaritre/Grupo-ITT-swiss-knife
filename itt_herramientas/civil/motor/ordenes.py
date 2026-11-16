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
import math
from typing import Any, Callable

from . import dxf as dxf_mod
from .cuadro import FORMATOS as FORMATOS_CUADRO
from .cuadro import OpcionesCuadro, calcular_cuadro, dibujar_cuadro, resumen_geo
from .cuadro_curvas import curvas_de_polilineas, dibujar_cuadro_curvas
from .anotacion import CAPAS_ANOTACION, CAPAS_RETICULA, anotar_lados, cuadricula_utm, intervalo_reticula, numerar_vertices
from .curvas import curvas_de_nivel, dibujar_curvas, suavizar
from .dibujos import APP, BLOQUES, CAPAS_PUNTOS, bloque_punto, dibujar_puntos, dibujar_tin
from .edicion_tin import ErrorEdicion, agregar, invertir, normalizar_cara, insertar_puntos, quitar_dentro, quitar_fuera, quitar_largos, revisar
from .eje import Eje, formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Insercion, Linea, Polilinea, Texto
from .escala import ALTURAS_MM, Escala
from .memoria import memoria_descriptiva, reporte_puntos
from .perfiles import PIV, Rasante, dibujar_perfil, perfil_terreno
from .puntos import PROYECTO, TERRENO, Punto, escribir_puntos, leer_puntos, numeros_repetidos
from .secciones import Plantilla, dibujar_secciones, secciones
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

def importar_puntos(d: dict) -> dict:
    datos = base64.b64decode(d.get("archivo") or b"")
    if not datos:
        raise ErrorOrden("Elige un archivo de puntos.")
    tipo = d.get("tipo", TERRENO)
    if tipo not in (TERRENO, PROYECTO):
        raise ErrorOrden("Elige terreno o proyecto.")
    try:
        lec = leer_puntos(datos, d.get("orden") or None, d.get("separador") or None, d.get("decimal") or None, tipo, d.get("prefijo", ""))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    pts = lec.puntos
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    sep = {",": "coma", "\t": "tabulador", ";": "punto y coma", " ": "espacios", "|": "barra"}.get(lec.separador, lec.separador)
    resumen = {
        "puntos": len(pts), "orden": lec.orden, "separador": sep, "coma_decimal": lec.decimal == ",", "codificacion": lec.codificacion,
        "orden_de": {"encabezado": "el encabezado", "utm": "las coordenadas"}.get(lec.orden_confianza, "tu elección"),
        "repetidos": numeros_repetidos(pts), "extension": [min(xs), min(ys), max(xs), max(ys)],
        "sin_elevacion": sum(1 for p in pts if p.z is None),
    }
    salida: dict[str, Any] = {
        "resumen": resumen,
        "muestra": [[p.numero, p.x, p.y, p.z, p.descripcion] for p in pts[:20]],
        "descartados": [{"linea": n, "texto": t[:120], "motivo": m} for n, t, m in lec.descartados[:200]],
        "avisos": ([f"{len(lec.descartados)} renglones no se leyeron."] if lec.descartados else [])
        + ([f"{len(resumen['repetidos'])} números de punto se repiten."] if resumen["repetidos"] else []),
    }
    if not d.get("solo_vista"):
        e = d.get("etiquetas") or {}
        salida["dxf"] = _dxf(dibujar_puntos(pts, _escala(d), int(d.get("decimales_z", 3)), e.get("numero", True), e.get("elevacion", True), e.get("descripcion", True)))
    return salida


def exportar_puntos(d: dict) -> dict:
    pts = _puntos(d.get("puntos"))
    if not pts:
        raise ErrorOrden("No hay puntos para exportar.")
    sep = {"coma": ",", "tabulador": "\t", "punto y coma": ";", "espacios": " ", "barra": "|"}.get(d.get("separador", "coma"), ",")
    try:
        datos = escribir_puntos(pts, d.get("orden", "PENZD"), sep, int(d.get("decimales", 3)), int(d.get("decimales_z", d.get("decimales", 3))),
                                "," if d.get("coma_decimal") else ".", bool(d.get("encabezado")), "\r\n")
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    return {"archivo": base64.b64encode(datos).decode("ascii"), "resumen": {"puntos": len(pts)}, "avisos": []}


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
                         e.get("cuales", "maestras") == "maestras", bool(e.get("hueco", True)), bool(d.get("tres_d")),
                         sufijo="_PROYECTO" if d.get("tipo") == PROYECTO else "")
    if e.get("cuales") == "ninguna":
        dib.entidades = [x for x in dib.entidades if x.tipo != "texto"]
    return {"dxf": _dxf(dib), "resumen": {"curvas": len(lista), "maestras": sum(1 for c in lista if c.maestra),
                                         "desde": min((c.elevacion for c in lista), default=None), "hasta": max((c.elevacion for c in lista), default=None)},
            "avisos": avisos}


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
    v, combas = _polilinea(d.get("eje"), "el eje")
    try:
        return Eje(v, combas, _num(d, "inicio", 0.0))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None


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


def perfil(d: dict) -> dict:
    tin = _tin_de_caras(_caras(d.get("caras")))
    eje = _eje(d)
    intervalo = _num(d, "intervalo", 20.0, minimo=0.1)
    pf = perfil_terreno(tin, eje, intervalo)
    if not any(p.z is not None for p in pf.puntos):
        raise ErrorOrden("El eje no pasa por la triangulación.")
    origen = d.get("origen")
    if not origen:
        x, y, _ = eje.punto(eje.inicio)
        origen = (min(p.x for p in pf.puntos), min(p.y for p in pf.puntos) - 40 * _escala(d).altura("cuadro_dato"))
    ras = _rasante(d)
    dib = dibujar_perfil(pf, (float(origen[0]), float(origen[1])), _escala(d), _num(d, "exageracion", 10.0, minimo=0.1, maximo=100), ras,
                         marcar_en_planta=False)
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
            "estaciones": [[p.estacion, p.z] for p in pf.redondas],
            "avisos": [f"{fuera} estaciones quedan fuera de la triangulación."] if fuera else []}


def _colocar(dib: Dibujo, esquina) -> None:
    """Con `esquina_superior` [x, y], mueve el dibujo para que su esquina superior izquierda quede ahí (ubicación automática de la página,
    a un lado de lo que ya hay en el plano)."""
    if not esquina:
        return
    caja = dib.caja()
    if caja:
        dib.trasladar(float(esquina[0]) - caja[0], float(esquina[1]) - caja[3])


def secciones_orden(d: dict) -> dict:
    tin = _tin_de_caras(_caras(d.get("caras")))
    eje = _eje(d)
    intervalo = _num(d, "intervalo", 20.0, minimo=0.1)
    izq, der = _num(d, "izquierda", 20.0, minimo=0.1), _num(d, "derecha", 20.0, minimo=0.1)
    ras = _rasante(d)
    pl = d.get("plantilla") or {}
    try:
        plantilla = Plantilla(_num(pl, "ancho_izq", 3.5, minimo=0), _num(pl, "ancho_der", 3.5, minimo=0), _num(pl, "bombeo", 2.0) / 100.0,
                              _num(pl, "talud_corte", 1.0, minimo=0.01), _num(pl, "talud_terraplen", 1.5, minimo=0.01))
    except ValueError as exc:
        raise ErrorOrden(str(exc)) from None
    lista = secciones(tin, eje, eje.estaciones(intervalo), izq, der, ras, plantilla)
    esc = _escala(d)
    origen = d.get("origen")
    if not origen:
        xs = [eje.punto(e)[0] for e in (eje.inicio, eje.fin)]
        ys = [eje.punto(e)[1] for e in (eje.inicio, eje.fin)]
        origen = (max(xs) + 20 * esc.altura("cuadro_dato"), max(ys))
    dib = dibujar_secciones(lista, (float(origen[0]), float(origen[1])), esc, _num(d, "exageracion", 1.0, minimo=0.1, maximo=20), int(d.get("columnas", 4)))
    resumen: dict[str, Any] = {"secciones": len(lista)}
    avisos = [a for s in lista for a in s.avisos][:30]
    if ras is not None:
        renglones = volumenes_por_secciones(lista, _num(d, "abundamiento", 1.0, minimo=0.01))
        resumen.update(corte=renglones[-1].acum_corte, terraplen=renglones[-1].acum_terraplen, ordenada_final=renglones[-1].ordenada)
        if d.get("tabla", True):
            h = esc.altura("cuadro_dato")
            xs = [e.inicio[0] for e in dib.entidades if hasattr(e, "inicio")] or [float(origen[0])]
            ys = [e.inicio[1] for e in dib.entidades if hasattr(e, "inicio")] or [float(origen[1])]
            dib.unir(dibujar_tabla_volumenes(renglones, (float(origen[0]), min(ys) - 6 * h), esc))
    _colocar(dib, d.get("esquina_superior"))
    return {"dxf": _dxf(dib), "resumen": resumen, "avisos": avisos}


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


ORDENES: dict[str, Callable[[dict], dict]] = {
    "importar_puntos": importar_puntos,
    "exportar_puntos": exportar_puntos,
    "redibujar_puntos": redibujar_puntos,
    "triangular": triangular,
    "invertir": invertir_triangulos,
    "quitar_triangulos": quitar_triangulos,
    "agregar_triangulo": agregar_triangulo,
    "revisar_tin": revisar_tin,
    "curvas": curvas,
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
}

#: Órdenes que pueden tardar con planos grandes: el servidor las manda a su cola de tareas.
PESADAS = {"triangular", "curvas", "perfil", "secciones", "volumen_superficies", "revisar_tin", "insertar_puntos"}


def ejecutar(nombre: str, datos: dict) -> dict:
    if nombre not in ORDENES:
        raise ErrorOrden("Orden desconocida.")
    if not isinstance(datos, dict):
        raise ErrorOrden("Datos no válidos.")
    return ORDENES[nombre](datos)
