"""Triangulación, pendientes y escurrimiento (herramientas-web.md §4.6).

Toda la geometría la calcula `dominio.triangulation` (la del escritorio, sin cambios de lógica); aquí se administran los archivos,
los límites (200 000 puntos, 400 000 triángulos, 100 MB) y la vista previa rasterizada para el navegador.
"""
from __future__ import annotations

import math
import re
import shutil
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from tvplataforma import ajustes
from tvplataforma.tareas import cancelada, progreso

from ..comun import errores
from ..dominio import triangulation as tri
from ..models import Triangulacion

MODOS_SUPERFICIE = ("puntos", "capas")
MAX_RANGOS = 20                         # los argumentos de una tarea caben en 8 KiB


class ErrorLimite(errores.ErrorUsuario):
    """El trabajo supera lo que la web procesa; el mensaje manda a la persona al módulo local."""


def raiz() -> Path:
    return Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))


def carpeta(t: Triangulacion) -> Path:
    return raiz() / "triangulacion" / str(t.cuenta_id) / str(t.id)


def ruta_origen(t: Triangulacion) -> Path:
    return raiz() / t.ruta_origen


#: Tope de la web por la memoria medida (docs/pwa.md): con 768 MB de cola, 75 000 puntos y 150 000 triángulos es lo más que cabe. Si el contrato
#: (`TV_HERRAMIENTAS_LIMITES`) trae un límite MÁS BAJO, manda el del contrato; uno más alto no pasa de este.
LIMITES_WEB = {"puntos": 75_000, "triangulos": 150_000}


def limites() -> dict:
    """Límites efectivos: el menor entre el contrato y el tope de memoria de la web."""
    contrato = {k: ajustes.limite_herramienta("triangulacion", k) for k in ("bytes", "puntos", "triangulos")}
    return {k: min(v, LIMITES_WEB.get(k, v)) for k, v in contrato.items()}


def _msg_limite(exc: tri.SuperaLimite) -> str:
    return (f"{exc} Supera lo que se procesa en la web. Ábrelo en Herramientas Grupo ITT (módulo local), "
            "que no tiene este límite.")


def _avance(inicio: float, ancho: float):
    def notificar(fraccion: float, mensaje: str) -> None:
        if cancelada():
            raise InterruptedError("Cancelado")
        progreso(inicio + ancho * fraccion, mensaje)
    return notificar


# -- Lectura del origen ---------------------------------------------------------------------------------------------------

def leer(t: Triangulacion) -> dict:
    """Cuenta lo que trae el DXF: puntos, bloques, vértices de polilíneas y capas con triángulos. No calcula nada."""
    import ezdxf

    try:
        documento = ezdxf.readfile(ruta_origen(t))
    except (OSError, ezdxf.DXFError) as exc:
        errores.log.warning("No se pudo leer el DXF de la triangulación %s: %s: %s", t.pk, type(exc).__name__, exc)
        raise errores.ErrorUsuario("No se pudo leer el DXF. Revisa que sea un DXF válido y que no esté dañado, y vuelve a subirlo.") from exc
    progreso(0.1, "Contando entidades…")
    puntos = inserts = vertices = caras = 0
    capas: dict[str, int] = {}
    xs, ys = [], []
    for e in documento.modelspace():
        tipo = e.dxftype()
        if tipo == "POINT":
            puntos += 1
            ubic = e.dxf.location
            xs.append(float(ubic.x)); ys.append(float(ubic.y))
        elif tipo == "INSERT":
            inserts += 1
            ubic = e.dxf.insert
            xs.append(float(ubic.x)); ys.append(float(ubic.y))
        elif tipo == "LWPOLYLINE":
            vertices += len(e)
            capas[str(e.dxf.layer)] = capas.get(str(e.dxf.layer), 0) + 1
        elif tipo == "POLYLINE":
            vertices += len(e.vertices)
            capas[str(e.dxf.layer)] = capas.get(str(e.dxf.layer), 0) + 1
        elif tipo == "3DFACE":
            caras += 1
            capas[str(e.dxf.layer)] = capas.get(str(e.dxf.layer), 0) + 1
    lim = limites()
    resumen = {"puntos": puntos, "inserts": inserts, "vertices_polilinea": vertices, "caras_3d": caras, "capas": [{"nombre": n, "entidades": c} for n, c in sorted(capas.items(), key=lambda x: x[0].casefold())],
               "limites": [min(xs), min(ys), max(xs), max(ys)] if xs else None, "avisos": []}
    if puntos + inserts > lim["puntos"]:
        resumen["avisos"].append(f"El archivo trae {puntos + inserts:,} puntos y bloques; la web procesa hasta {lim['puntos']:,} puntos únicos. Si hay duplicados puede entrar; si no, usa el módulo local.")
    if not (puntos or inserts or vertices or caras):
        raise ValueError("El DXF no trae POINT, INSERT, polilíneas ni 3DFACE: no hay de dónde sacar una superficie.")
    return resumen


# -- Superficie -----------------------------------------------------------------------------------------------------------------

def _guardar_npz(ruta: Path, **arreglos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.stem + ".tmp.npz")
    np.savez_compressed(temporal, **arreglos)
    temporal.replace(ruta)


def _cargar(ruta: Path) -> dict:
    with np.load(ruta) as f:
        return {k: f[k] for k in f.files}


def _borrar_analisis(t: Triangulacion) -> None:
    for nombre in ("pendientes.npz", "flujo.npz", "salida.dxf"):
        (carpeta(t) / nombre).unlink(missing_ok=True)
    t.pendientes, t.escurrimiento, t.salida_ruta, t.salida_tipo, t.salida_nombre = {}, {}, "", "", ""


def _num(valor, nombre: str, *, minimo=None, maximo=None, por_omision=None, entero=False):
    texto = str(valor if valor is not None else "").strip().replace(",", ".")
    if texto == "":
        if por_omision is None:
            raise ValueError(f"Falta «{nombre}».")
        return por_omision
    try:
        n = int(float(texto)) if entero else float(texto)
    except ValueError:
        raise ValueError(f"«{nombre}» debe ser un número.") from None
    if not math.isfinite(n) or (minimo is not None and n < minimo) or (maximo is not None and n > maximo):
        raise ValueError(f"«{nombre}» está fuera de rango.")
    return n


def opciones_superficie(post) -> dict:
    modo = post.get("modo", "puntos")
    if modo not in MODOS_SUPERFICIE:
        raise ValueError("El origen de la superficie debe ser puntos o capas de triángulos.")
    if modo == "capas":
        return {"modo": "capas", "capas": [c for c in post.getlist("capa") if c]}
    o = {"modo": "puntos", "puntos": "puntos" in post, "inserts": "inserts" in post, "polilineas": "polilineas" in post,
         "decimales": _num(post.get("decimales"), "decimales", minimo=0, maximo=12, por_omision=6, entero=True),
         "arista_max": _num(post.get("arista_max"), "arista máxima", minimo=1e-9, por_omision=-1.0),
         "area_min": _num(post.get("area_min"), "área mínima", minimo=0, por_omision=1e-6)}
    if not (o["puntos"] or o["inserts"] or o["polilineas"]):
        raise ValueError("Activa al menos un tipo de entidad para obtener puntos.")
    o["arista_max"] = None if o["arista_max"] == -1.0 else o["arista_max"]
    return o


def calcular_superficie(t: Triangulacion, o: dict) -> None:
    lim = limites()
    progreso(0.02, "Leyendo el DXF…")
    try:
        if o["modo"] == "puntos":
            modelo = tri.create_tin_model(ruta_origen(t), o["puntos"], o["inserts"], o["polilineas"], o["decimales"], o["arista_max"], o["area_min"],
                                          progress=_avance(0.02, 0.8), max_points=lim["puntos"])
            triangulos, puntos, estadisticas = modelo.triangles, modelo.points, asdict(modelo.stats)
            if len(triangulos) > lim["triangulos"]:
                raise tri.SuperaLimite("Triángulos del TIN", len(triangulos), lim["triangulos"])
        else:
            triangulos = tri.extract_triangles(ruta_origen(t), o["capas"] or None, progress=_avance(0.02, 0.8), max_triangles=lim["triangulos"])
            puntos, estadisticas = [], {}
    except tri.SuperaLimite as exc:
        raise ErrorLimite(_msg_limite(exc)) from exc
    progreso(0.85, "Guardando la superficie…")
    arr = np.asarray(triangulos, dtype=np.float64)                                            # (M, 3, 3)
    pts = np.asarray(puntos, dtype=np.float64).reshape(-1, 3)
    _borrar_analisis(t)
    _guardar_npz(carpeta(t) / "superficie.npz", triangulos=arr, puntos=pts)
    todos = pts if len(pts) else arr.reshape(-1, 3)
    t.superficie = {"origen": o["modo"], "opciones": o, "puntos": int(len(pts)), "triangulos": int(len(arr)), "z": [float(todos[:, 2].min()), float(todos[:, 2].max())],
                    "limites": [float(todos[:, 0].min()), float(todos[:, 1].min()), float(todos[:, 0].max()), float(todos[:, 1].max())], "estadisticas": estadisticas}
    t.save(update_fields=["superficie", "pendientes", "escurrimiento", "salida_ruta", "salida_tipo", "salida_nombre"])
    progreso(1.0, "Superficie lista")


def triangulos_guardados(t: Triangulacion) -> list:
    if not t.superficie:
        raise ValueError("Primero calcula la superficie (TIN o triángulos del DXF).")
    arr = _cargar(carpeta(t) / "superficie.npz")["triangulos"]
    return [(tuple(a), tuple(b), tuple(c)) for a, b, c in arr.tolist()]


def modelo_tin(t: Triangulacion) -> tri.TinModel:
    datos = _cargar(carpeta(t) / "superficie.npz")
    puntos = [tuple(p) for p in datos["puntos"].tolist()]
    triangulos = [(tuple(a), tuple(b), tuple(c)) for a, b, c in datos["triangulos"].tolist()]
    return tri.TinModel(t.nombre, puntos, triangulos, tri.TinStats(**{k: v for k, v in (t.superficie.get("estadisticas") or {}).items() if k in tri.TinStats.__dataclass_fields__}))


# -- Pendientes -------------------------------------------------------------------------------------------------------------------

def _color(valor: str, nombre: str) -> str:
    v = str(valor).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        raise ValueError(f"Color inválido en {nombre}: usa #RRGGBB.")
    return v.upper()


def rangos_pendientes(post) -> list[tri.SlopeRange]:
    nombres, desde, hasta, colores = (post.getlist(k) for k in ("r_nombre", "r_desde", "r_hasta", "r_color"))
    if not nombres or not (len(nombres) == len(desde) == len(hasta) == len(colores)):
        raise ValueError("Agrega al menos un rango de pendiente.")
    if len(nombres) > MAX_RANGOS:
        raise ValueError(f"Usa hasta {MAX_RANGOS} rangos.")
    salida = []
    for n, d, h, c in zip(nombres, desde, hasta, colores):
        nombre = " ".join(n.split())[:60] or "Rango"
        salida.append(tri.SlopeRange(nombre, _num(d, f"desde de {nombre}", minimo=0), None if str(h).strip() == "" else _num(h, f"hasta de {nombre}", minimo=0), _color(c, nombre)))
    return tri.validate_ranges(salida)


def rangos_flujo(post) -> list[tri.FlowRange]:
    nombres, desde, hasta, colores, factores, grosores = (post.getlist(k) for k in ("r_nombre", "r_desde", "r_hasta", "r_color", "r_factor", "r_grosor"))
    if not nombres or not (len(nombres) == len(desde) == len(hasta) == len(colores) == len(factores) == len(grosores)):
        raise ValueError("Agrega al menos un rango para las flechas.")
    if len(nombres) > MAX_RANGOS:
        raise ValueError(f"Usa hasta {MAX_RANGOS} rangos.")
    salida = []
    for n, d, h, c, f, g in zip(nombres, desde, hasta, colores, factores, grosores):
        nombre = " ".join(n.split())[:60] or "Rango"
        salida.append(tri.FlowRange(nombre, _num(d, f"desde de {nombre}", minimo=0), None if str(h).strip() == "" else _num(h, f"hasta de {nombre}", minimo=0), _color(c, nombre),
                                    _num(f, f"longitud de {nombre}", minimo=1e-6, maximo=20), _num(g, f"grosor de {nombre}", minimo=1e-6, maximo=5)))
    return tri.validate_flow_ranges(salida)


def calcular_pendientes(t: Triangulacion, rangos: list[tri.SlopeRange], decimales: int) -> None:
    triangulos = triangulos_guardados(t)
    progreso(0.1, "Calculando pendientes…")
    analisis = tri.analyze_slopes(triangulos, rangos, t.nombre, progress=_avance(0.1, 0.8))
    _guardar_npz(carpeta(t) / "pendientes.npz", pendientes=np.asarray(analisis.slopes, dtype=np.float64), asignaciones=np.asarray(analisis.assignments, dtype=np.int32))
    total = analisis.total_area
    t.pendientes = {"decimales": decimales, "area_total": total, "rangos": [{**asdict(r), "etiqueta": r.label(), "porcentaje": (r.area_2d / total * 100 if total else 0.0)} for r in analisis.ranges]}
    t.salida_ruta = ""
    t.save(update_fields=["pendientes", "salida_ruta"])
    progreso(1.0, "Zonificación lista")


def analisis_pendientes(t: Triangulacion) -> tri.SlopeAnalysis:
    d = _cargar(carpeta(t) / "pendientes.npz")
    rangos = [tri.SlopeRange(**{k: v for k, v in r.items() if k in tri.SlopeRange.__dataclass_fields__}) for r in t.pendientes["rangos"]]
    return tri.SlopeAnalysis(t.nombre, triangulos_guardados(t), d["pendientes"].tolist(), d["asignaciones"].tolist(), rangos)


# -- Escurrimiento ------------------------------------------------------------------------------------------------------------------

def opciones_flujo(post) -> dict:
    return {"longitud_base": _num(post.get("longitud_base"), "longitud base", minimo=1e-9, por_omision=-1.0),
            "punta": _num(post.get("punta"), "tamaño de punta", minimo=10, maximo=60, por_omision=28.0),
            "densidad": _num(post.get("densidad"), "densidad", minimo=1, maximo=1000, por_omision=1, entero=True),
            "pendiente_min": _num(post.get("pendiente_min"), "pendiente mínima", minimo=0, por_omision=0.10)}


def calcular_escurrimiento(t: Triangulacion, rangos: list[tri.FlowRange], o: dict) -> None:
    triangulos = triangulos_guardados(t)
    progreso(0.1, "Calculando escurrimientos…")
    base = None if o["longitud_base"] == -1.0 else o["longitud_base"]
    analisis = tri.analyze_flow(triangulos, rangos, t.nombre, base_length=base, head_ratio=o["punta"] / 100.0, density=o["densidad"], minimum_slope=o["pendiente_min"], progress=_avance(0.1, 0.8))
    flechas = np.asarray([[*a.start, *a.tip, *a.head_left, *a.head_right, *a.center, a.slope_pct, a.range_index] for a in analisis.arrows], dtype=np.float64)
    _guardar_npz(carpeta(t) / "flujo.npz", flechas=flechas)
    t.escurrimiento = {"opciones": o, "flechas": int(len(flechas)), "longitud_base": analisis.base_length, "punta": analisis.head_ratio, "densidad": analisis.density,
                       "pendiente_min": analisis.minimum_slope, "rangos": [{**asdict(r), "etiqueta": r.label()} for r in analisis.ranges]}
    t.salida_ruta = ""
    t.save(update_fields=["escurrimiento", "salida_ruta"])
    progreso(1.0, "Escurrimientos listos")


def analisis_flujo(t: Triangulacion) -> tri.FlowAnalysis:
    f = _cargar(carpeta(t) / "flujo.npz")["flechas"]
    flechas = [tri.FlowArrow((r[0], r[1]), (r[2], r[3]), (r[4], r[5]), (r[6], r[7]), (r[8], r[9], r[10]), r[11], int(r[12])) for r in f.tolist()]
    rangos = [tri.FlowRange(**{k: v for k, v in r.items() if k in tri.FlowRange.__dataclass_fields__}) for r in t.escurrimiento["rangos"]]
    e = t.escurrimiento
    return tri.FlowAnalysis(t.nombre, triangulos_guardados(t), flechas, rangos, e["longitud_base"], e["punta"], e["densidad"], e["pendiente_min"])


# -- Exportación DXF --------------------------------------------------------------------------------------------------------------------

SUFIJOS = {"tin": "TIN", "pendientes": "ZONIFICACION_PENDIENTES", "escurrimiento": "ESCURRIMIENTOS"}


def nombre_salida(t: Triangulacion, tipo: str) -> str:
    return f"{Path(t.nombre).stem}_{SUFIJOS[tipo]}.dxf"


def exportar(t: Triangulacion, tipo: str, post: dict) -> Path:
    if tipo not in SUFIJOS:
        raise ValueError("Elige TIN, pendientes o escurrimiento.")
    destino = carpeta(t) / "salida.dxf"
    temporal = carpeta(t) / "salida.tmp.dxf"
    avance = _avance(0.05, 0.9)
    if tipo == "tin":
        if not t.superficie or t.superficie.get("origen") != "puntos":
            raise ValueError("El DXF del TIN se genera a partir de puntos: calcula la superficie desde puntos.")
        tri.write_tin_dxf(modelo_tin(t), temporal, bool(post.get("conservar_puntos", True)), avance)
    elif tipo == "pendientes":
        if not t.pendientes:
            raise ValueError("Calcula primero las pendientes.")
        tri.write_slope_dxf(analisis_pendientes(t), temporal, bool(post.get("texto_pendiente", False)), bool(post.get("caras", True)), int(post.get("decimales", t.pendientes.get("decimales", 2))), avance)
    else:
        if not t.escurrimiento:
            raise ValueError("Calcula primero los escurrimientos.")
        tri.write_flow_dxf(analisis_flujo(t), temporal, bool(post.get("referencia_tin", True)), bool(post.get("texto_pendiente", False)), avance)
    temporal.replace(destino)
    t.salida_ruta, t.salida_tipo, t.salida_nombre = destino.relative_to(raiz()).as_posix(), tipo, nombre_salida(t, tipo)
    t.save(update_fields=["salida_ruta", "salida_tipo", "salida_nombre"])
    progreso(1.0, "DXF listo")
    return destino


def borrar_archivos(t: Triangulacion) -> None:
    shutil.rmtree(carpeta(t), ignore_errors=True)
