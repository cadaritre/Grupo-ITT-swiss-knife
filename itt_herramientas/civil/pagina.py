"""Lo que la plantilla de Civil necesita (menús, listas y configuración de la página)."""
from __future__ import annotations

import hashlib
from functools import lru_cache

from django.urls import reverse

from .motor.dibujos import BLOQUES, CAPAS_PUNTOS, CAPAS_TIN
from .motor.escala import ALTURAS_MM, ESCALAS_COMUNES
from .motor.geodesia import ZONAS_MEXICO
from .motor.puntos import COLUMNAS, ORDENES

MARCA = "00000000-0000-0000-0000-000000000000"

#: Menús de Civil, en el orden de la especificación (docs/civil.md): la pestaña «Civil» de la cinta del editor se arma de aquí (un grupo por
#: menú; `cinta()`), y también la barra de respaldo para un editor sin pestañas propias. Cada acción: `clave` (la de civil.js), `nombre`,
#: `icono` (Lucide) y, si hace falta, `corto` (su nombre en la cinta, donde el grupo ya dice de qué es: «Exportar» en Puntos), `comando` (lo que se escribe en la línea de comandos; por omisión «CV» + la clave sin guiones bajos),
#: `grande` (botón grande en la cinta), `solo_lee` (funciona sin el control del plano) y `descripcion` (la ayuda del botón). Un menú con
#: `como: "desplegable"` es un solo botón grande con sus acciones en la lista.
MENUS = (
    {"clave": "puntos", "nombre": "Puntos", "icono": "map-pin", "acciones": (
        {"clave": "importar", "nombre": "Importar puntos…", "icono": "file-input", "comando": "CVIMPORTARPUNTOS", "grande": True,
         "descripcion": "Lee un archivo de puntos (PNEZD, PENZD, NEZ…) y los dibuja con número, elevación y descripción."},
        {"clave": "exportar", "nombre": "Exportar puntos…", "corto": "Exportar", "icono": "file-output", "comando": "CVEXPORTARPUNTOS", "solo_lee": True},
        {"separador": True},
        {"clave": "punto_nuevo", "nombre": "Dibujar punto…", "icono": "map-pin-plus", "comando": "CVDIBUJARPUNTO"},
        {"clave": "punto_editar", "nombre": "Editar punto…", "icono": "map-pin-pen", "comando": "CVEDITARPUNTO"},
        {"clave": "etiquetas_puntos", "nombre": "Etiquetas de puntos…", "icono": "tags"},
        {"clave": "convertir_puntos", "nombre": "Convertir puntos…", "icono": "arrow-left-right"},
        {"clave": "renumerar", "nombre": "Renumerar puntos…", "icono": "list-ordered"},
        {"clave": "localizar", "nombre": "Localizar punto…", "icono": "locate-fixed", "solo_lee": True},
        {"separador": True},
        {"clave": "reporte", "nombre": "Reporte de puntos…", "corto": "Reporte", "icono": "sheet", "comando": "CVREPORTEPUNTOS", "solo_lee": True},
    )},
    {"clave": "terreno", "nombre": "Triangulación", "icono": "triangle", "acciones": (
        {"clave": "triangular", "nombre": "Triangular…", "icono": "triangle", "grande": True,
         "descripcion": "Triangula los puntos de la superficie, con líneas de quiebre, límite e islas."},
        {"separador": True},
        {"clave": "invertir", "nombre": "Invertir triángulos", "icono": "flip-horizontal-2"},
        {"clave": "borrar", "nombre": "Borrar triángulos…", "icono": "eraser", "comando": "CVBORRARTRIANGULOS"},
        {"clave": "agregar", "nombre": "Agregar triángulo", "icono": "square-plus", "comando": "CVAGREGARTRIANGULO"},
        {"clave": "insertar", "nombre": "Insertar puntos…", "icono": "circle-plus", "comando": "CVINSERTARPUNTOS"},
        {"separador": True},
        {"clave": "revisar", "nombre": "Revisar triangulación…", "corto": "Revisar", "icono": "scan-search", "comando": "CVREVISARTIN"},
    )},
    {"clave": "curvas", "nombre": "Curvas", "icono": "waves", "acciones": (
        {"clave": "curvas", "nombre": "Curvas de nivel…", "icono": "waves", "grande": True,
         "descripcion": "Curvas de nivel de la triangulación: intervalo, maestras, suavizado y etiquetas."},
        {"clave": "actualizar_curvas", "nombre": "Actualizar curvas", "corto": "Actualizar", "icono": "refresh-cw"},
        {"separador": True},
        {"clave": "etiquetar_curvas", "nombre": "Etiquetar curvas…", "corto": "Etiquetar", "icono": "tag"},
        {"clave": "suavizar_curvas", "nombre": "Suavizar curvas…", "corto": "Suavizar", "icono": "spline-pointer"},
        {"clave": "unir_curvas", "nombre": "Unir curvas cortadas…", "corto": "Unir cortadas", "icono": "link"},
    )},
    {"clave": "cuadros", "nombre": "Cuadros", "icono": "table", "acciones": (
        {"clave": "cuadro", "nombre": "Cuadro de construcción…", "icono": "table", "grande": True,
         "descripcion": "Rumbos, distancias, coordenadas y superficie de un polígono (normal, RAN o UTM-GPS)."},
        {"clave": "cuadro_curvas", "nombre": "Cuadro de curvas…", "icono": "spline"},
        {"separador": True},
        {"clave": "rumbos", "nombre": "Rumbo y distancia de lados…", "corto": "Rumbos", "icono": "ruler-dimension-line"},
        {"clave": "numerar", "nombre": "Numerar vértices…", "icono": "hash", "comando": "CVNUMERARVERTICES"},
        {"separador": True},
        {"clave": "memoria", "nombre": "Memoria descriptiva…", "corto": "Memoria", "icono": "file-text", "solo_lee": True},
    )},
    {"clave": "plano", "nombre": "Plano", "icono": "grid-3x3", "acciones": (
        {"clave": "cuadricula", "nombre": "Cuadrícula UTM…", "icono": "grid-3x3"},
        {"clave": "lotes", "nombre": "Lotificar manzana…", "corto": "Lotificar", "icono": "layout-grid", "comando": "CVLOTIFICAR"},
        {"separador": True},
        {"clave": "importar_kml", "nombre": "Importar KML o KMZ…", "corto": "Importar KML", "icono": "earth", "comando": "CVIMPORTARKML",
         "descripcion": "Polígonos, líneas y puntos de Google Earth (KML o KMZ) a la zona UTM del plano, con sus carpetas y nombres."},
        {"clave": "exportar_kml", "nombre": "Exportar KML o KMZ…", "corto": "Exportar KML", "icono": "file-output", "comando": "CVEXPORTARKML", "solo_lee": True,
         "descripcion": "Las polilíneas elegidas a Google Earth (KML o KMZ), con su nombre y superficie en m² y ha-a-ca."},
    )},
    {"clave": "ejes", "nombre": "Perfiles", "icono": "chart-line", "acciones": (
        {"clave": "eje_espirales", "nombre": "Eje con espirales…", "corto": "Eje", "icono": "route", "comando": "CVEJE"},
        {"separador": True},
        {"clave": "perfil", "nombre": "Perfil del eje…", "corto": "Perfil", "icono": "chart-line", "grande": True,
         "descripcion": "Perfil del terreno a lo largo de un eje, con cadenamiento y rasante."},
        {"clave": "secciones", "nombre": "Secciones transversales…", "corto": "Secciones", "icono": "panels-top-left", "grande": True,
         "descripcion": "Secciones del terreno a lo largo del eje, con plantilla, taludes y áreas."},
    )},
    {"clave": "volumenes", "nombre": "Volúmenes", "icono": "box", "como": "desplegable", "acciones": (
        {"clave": "secciones", "nombre": "Por secciones…", "icono": "panels-top-left"},
        {"clave": "volumen", "nombre": "Entre superficies…", "icono": "box", "grande": True,
         "descripcion": "Corte y terraplén entre la triangulación del terreno y la de proyecto."},
        {"separador": True},
        {"clave": "masa", "nombre": "Curva masa…", "icono": "chart-spline", "comando": "CVCURVAMASA"},
    )},
    # Imágenes del plano: las de la carpeta del proyecto (cad/local/). Otra fuente de imágenes se agrega aquí como cualquier acción.
    {"clave": "imagenes", "nombre": "Imágenes", "icono": "image", "acciones": (
        {"clave": "imagen", "nombre": "Insertar imagen georreferenciada…", "corto": "Imagen georreferenciada", "icono": "image-plus", "comando": "CVIMAGEN", "grande": True,
         "descripcion": "Coloca una imagen u ortofoto de la carpeta del proyecto con su archivo de mundo o GeoTIFF. Nada se sube."},
        {"clave": "referencias", "nombre": "Referencias externas…", "corto": "Referencias", "icono": "folder-open", "comando": "CVREFERENCIAS", "solo_lee": True,
         "descripcion": "Imágenes y dibujos de referencia del plano y la carpeta del proyecto en esta computadora."},
    )},
)

NOMBRES_ALTURA = {
    "punto_numero": "Número de punto", "punto_elevacion": "Elevación de punto", "punto_descripcion": "Descripción de punto",
    "punto_simbolo": "Símbolo de punto", "curva_etiqueta": "Elevación de curvas", "cuadro_titulo": "Título de cuadros",
    "cuadro_encabezado": "Encabezados de cuadros", "cuadro_dato": "Datos de cuadros y perfiles", "cuadro_superficie": "Superficie",
    "vertice_numero": "Número de vértice", "lado_rumbo_distancia": "Rumbo y distancia de lados",
}


#: Prefijo de los comandos de Civil en la línea de comandos del editor (CVTRIANGULAR, CVCURVAS…): uno corto y propio, como los de los
#: complementos de AutoCAD, que no choca con ningún comando del editor.
PREFIJO = "CV"

#: El botón de la escala del plano en la cinta (su texto es la escala vigente, «1:1000»).
ESCALA = {"clave": "escala", "comando": "CVESCALA", "nombre": "Escala", "icono": "ruler", "solo_lee": True,
          "descripcion": "Escala del plano, zona UTM y altura de los textos en el papel."}


def comando(accion: dict) -> str:
    """El comando de una acción: el suyo o «CV» + su clave sin guiones bajos (`etiquetar_curvas` → CVETIQUETARCURVAS)."""
    return accion.get("comando") or PREFIJO + accion["clave"].upper().replace("_", "")


def _svg(nombre: str) -> str:
    from tvplataforma.templatetags.tv import icono

    return str(icono(nombre))


def _accion_cinta(a: dict, seccion: int = 0) -> dict:
    nombre = a["nombre"].rstrip("…").strip()
    descripcion = a.get("descripcion", "")
    if a.get("corto") and a["corto"] != nombre:                          # la ayuda del botón dice el nombre completo
        descripcion = f"{nombre}. {descripcion}" if descripcion else nombre
    return {"clave": a["clave"], "comando": comando(a), "nombre": a.get("corto") or nombre, "icono": _svg(a["icono"]),
            "grande": bool(a.get("grande")), "solo_lee": bool(a.get("solo_lee")), "descripcion": descripcion, "seccion": seccion}


def _acciones_cinta(acciones) -> list[dict]:
    """Las acciones de un menú, cada una con su sección (las parten los separadores del menú)."""
    salida, seccion = [], 0
    for a in acciones:
        if a.get("separador"):
            seccion += 1
        else:
            salida.append(_accion_cinta(a, seccion))
    return salida


def cinta() -> dict:
    """La pestaña «Civil» de la cinta del editor, de `MENUS`: un grupo por menú con sus acciones, cada una con su comando, su icono SVG y su
    sección (los separadores del menú). La arma `static/herramientas/cad/civil-cinta.js`; lo que se agregue a `MENUS` aparece solo."""
    return {
        "prefijo": PREFIJO,
        "escala": _accion_cinta(ESCALA),
        "grupos": [{"clave": m["clave"], "nombre": m["nombre"], "como": m.get("como", ""),
                    "acciones": _acciones_cinta(m["acciones"])} for m in MENUS],
    }


def contexto_panel() -> dict:
    """Lo que necesita la barra y las hojas de Civil (`civil_panel.html`): igual para todas las cuentas y todos los planos."""
    return {
        "menus": MENUS,
        "cinta": cinta(),
        "escalas": ESCALAS_COMUNES,
        "alturas": [{"clave": k, "nombre": NOMBRES_ALTURA.get(k, k), "mm": v} for k, v in ALTURAS_MM.items() if k in NOMBRES_ALTURA],
        "ordenes": ORDENES,
        "columnas": tuple(COLUMNAS.items()),
        "intervalos": ("0.1", "0.25", "0.5", "1", "2", "5", "10", "20"),
        "maestras": (2, 4, 5, 10),
        "decimales": (0, 1, 2, 3, 4),
        "decimales_geo": (4, 5, 6, 7, 8, 9, 10),
        "iconos": ("triangle-alert", "circle-alert", "x", "trash-2", "plus"),
        "zonas": ZONAS_MEXICO,
    }


def _panel() -> tuple[str, str]:
    from django.template.loader import render_to_string

    html = render_to_string("herramientas/cad/civil_panel.html", {"civil": contexto_panel()})
    return html, hashlib.sha256(html.encode()).hexdigest()[:16]


_panel_guardado = lru_cache(maxsize=1)(_panel)


def panel() -> tuple[str, str]:
    """La barra y las hojas de Civil ya hechas, con su huella. No cambian hasta el siguiente despliegue (en desarrollo, en cada petición)."""
    from django.conf import settings

    return _panel() if settings.DEBUG else _panel_guardado()


def contexto(plano) -> dict:
    """Lo de la página del plano: la dirección del panel (con su huella, se guarda un año) y la configuración de este plano."""
    return {
        "panel": reverse("herramientas:cad_panel_civil") + f"?v={panel()[1]}",
        "config": {
            "plano": str(plano.pk),
            "urls": {"orden": reverse("herramientas:civil_orden", args=["__ORDEN__"]), "tarea": reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA}),
                     "formatos": reverse("herramientas:civil_formatos")},
            "alturas_mm": dict(ALTURAS_MM),
            "capas": {"puntos": {k: CAPAS_PUNTOS[k].nombre for k in ("terreno", "proyecto")}, "tin": {k: c.nombre for k, c in CAPAS_TIN.items()}},
            "bloques": dict(BLOQUES),
            "columnas": [[k, n] for k, n in COLUMNAS.items()],
            "ordenes": dict(ORDENES),
        },
    }
