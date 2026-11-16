"""Lo que la plantilla de Civil necesita (menús, listas y configuración de la página)."""
from __future__ import annotations

from django.urls import reverse

from .motor.dibujos import BLOQUES, CAPAS_PUNTOS, CAPAS_TIN
from .motor.escala import ALTURAS_MM, ESCALAS_COMUNES
from .motor.geodesia import ZONAS_MEXICO
from .motor.puntos import ORDENES

MARCA = "00000000-0000-0000-0000-000000000000"

#: Menús de la barra «Civil», en el orden de la especificación (docs/civil.md).
MENUS = (
    {"clave": "puntos", "nombre": "Puntos", "icono": "map-pin", "acciones": (
        {"clave": "importar", "nombre": "Importar puntos…", "icono": "file-input"},
        {"clave": "exportar", "nombre": "Exportar puntos…", "icono": "file-output"},
        {"separador": True},
        {"clave": "punto_nuevo", "nombre": "Dibujar punto…", "icono": "map-pin-plus"},
        {"clave": "punto_editar", "nombre": "Editar punto…", "icono": "map-pin-pen"},
        {"clave": "etiquetas_puntos", "nombre": "Etiquetas de puntos…", "icono": "tags"},
        {"clave": "convertir_puntos", "nombre": "Convertir puntos…", "icono": "arrow-left-right"},
        {"clave": "renumerar", "nombre": "Renumerar puntos…", "icono": "list-ordered"},
        {"clave": "localizar", "nombre": "Localizar punto…", "icono": "locate-fixed"},
        {"separador": True},
        {"clave": "reporte", "nombre": "Reporte de puntos…", "icono": "sheet"},
    )},
    {"clave": "terreno", "nombre": "Triangulación", "icono": "triangle", "acciones": (
        {"clave": "triangular", "nombre": "Triangular…", "icono": "triangle"},
        {"separador": True},
        {"clave": "invertir", "nombre": "Invertir triángulos", "icono": "flip-horizontal-2"},
        {"clave": "borrar", "nombre": "Borrar triángulos…", "icono": "eraser"},
        {"clave": "agregar", "nombre": "Agregar triángulo", "icono": "square-plus"},
        {"clave": "insertar", "nombre": "Insertar puntos…", "icono": "circle-plus"},
        {"separador": True},
        {"clave": "revisar", "nombre": "Revisar triangulación…", "icono": "scan-search"},
    )},
    {"clave": "curvas", "nombre": "Curvas", "icono": "waves", "acciones": (
        {"clave": "curvas", "nombre": "Curvas de nivel…", "icono": "waves"},
        {"clave": "actualizar_curvas", "nombre": "Actualizar curvas", "icono": "refresh-cw"},
        {"separador": True},
        {"clave": "etiquetar_curvas", "nombre": "Etiquetar curvas…", "icono": "tag"},
        {"clave": "suavizar_curvas", "nombre": "Suavizar curvas…", "icono": "spline-pointer"},
    )},
    {"clave": "cuadros", "nombre": "Cuadros", "icono": "table", "acciones": (
        {"clave": "cuadro", "nombre": "Cuadro de construcción…", "icono": "table"},
        {"clave": "cuadro_curvas", "nombre": "Cuadro de curvas…", "icono": "spline"},
        {"separador": True},
        {"clave": "rumbos", "nombre": "Rumbo y distancia de lados…", "icono": "ruler-dimension-line"},
        {"clave": "numerar", "nombre": "Numerar vértices…", "icono": "hash"},
        {"separador": True},
        {"clave": "memoria", "nombre": "Memoria descriptiva…", "icono": "file-text"},
    )},
    {"clave": "plano", "nombre": "Plano", "icono": "grid-3x3", "acciones": (
        {"clave": "cuadricula", "nombre": "Cuadrícula UTM…", "icono": "grid-3x3"},
        {"clave": "lotes", "nombre": "Lotificar manzana…", "icono": "layout-grid"},
    )},
    {"clave": "ejes", "nombre": "Perfiles", "icono": "chart-line", "acciones": (
        {"clave": "eje_espirales", "nombre": "Eje con espirales…", "icono": "route"},
        {"separador": True},
        {"clave": "perfil", "nombre": "Perfil del eje…", "icono": "chart-line"},
        {"clave": "secciones", "nombre": "Secciones transversales…", "icono": "panels-top-left"},
    )},
    {"clave": "volumenes", "nombre": "Volúmenes", "icono": "box", "acciones": (
        {"clave": "secciones", "nombre": "Por secciones…", "icono": "panels-top-left"},
        {"clave": "volumen", "nombre": "Entre superficies…", "icono": "box"},
        {"separador": True},
        {"clave": "masa", "nombre": "Curva masa…", "icono": "chart-spline"},
    )},
)

NOMBRES_ALTURA = {
    "punto_numero": "Número de punto", "punto_elevacion": "Elevación de punto", "punto_descripcion": "Descripción de punto",
    "punto_simbolo": "Símbolo de punto", "curva_etiqueta": "Elevación de curvas", "cuadro_titulo": "Título de cuadros",
    "cuadro_encabezado": "Encabezados de cuadros", "cuadro_dato": "Datos de cuadros y perfiles", "cuadro_superficie": "Superficie",
    "vertice_numero": "Número de vértice", "lado_rumbo_distancia": "Rumbo y distancia de lados",
}


def contexto(plano) -> dict:
    return {
        "menus": MENUS,
        "escalas": ESCALAS_COMUNES,
        "alturas": [{"clave": k, "nombre": NOMBRES_ALTURA.get(k, k), "mm": v} for k, v in ALTURAS_MM.items() if k in NOMBRES_ALTURA],
        "ordenes": ORDENES,
        "intervalos": ("0.1", "0.25", "0.5", "1", "2", "5", "10", "20"),
        "maestras": (2, 4, 5, 10),
        "decimales": (0, 1, 2, 3, 4),
        "iconos": ("triangle-alert", "circle-alert"),
        "zonas": ZONAS_MEXICO,
        "config": {
            "plano": str(plano.pk),
            "urls": {"orden": reverse("herramientas:civil_orden", args=["__ORDEN__"]), "tarea": reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA})},
            "alturas_mm": dict(ALTURAS_MM),
            "capas": {"puntos": {k: CAPAS_PUNTOS[k].nombre for k in ("terreno", "proyecto")}, "tin": {k: c.nombre for k, c in CAPAS_TIN.items()}},
            "bloques": dict(BLOQUES),
        },
    }
