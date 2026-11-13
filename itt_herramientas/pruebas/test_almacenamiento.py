from __future__ import annotations

import threading

import pytest

from itt_herramientas.dominio import almacenamiento as alm
from itt_herramientas.dominio import branding, document_texts, fuentes, quote_theme


def test_ajustes_por_omision_son_los_de_fabrica():
    assert alm.SETTINGS.get("geospatial.utm_zone") == 13
    assert alm.SETTINGS.get("no.existe", "x") == "x"
    assert branding.active_profile().key == "grupo_itt"


def test_ajustes_activos_se_aplican_solo_dentro_del_bloque():
    with alm.ajustes_activos({"branding": {"profile": "tresvizo"}, "documents": {"quote_palette": {"primary": "#112233"}}}):
        assert branding.active_profile().key == "tresvizo"
        assert quote_theme.quote_palette().primary_hex.upper() == "#112233"
        # lo no indicado conserva el valor de fábrica
        assert alm.SETTINGS.get("geospatial.hemisphere") == "N"
    assert branding.active_profile().key == "grupo_itt"


def test_ajustes_activos_no_se_mezclan_entre_hilos():
    vistos = {}
    listo = threading.Barrier(2)

    def trabajo(nombre, perfil):
        with alm.ajustes_activos({"branding": {"profile": perfil}}):
            listo.wait(timeout=5)
            vistos[nombre] = branding.active_profile().key

    hilos = [threading.Thread(target=trabajo, args=("a", "tresvizo")), threading.Thread(target=trabajo, args=("b", "grupo_itt"))]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert vistos == {"a": "tresvizo", "b": "grupo_itt"}


def test_los_valores_leidos_son_copias():
    valor = alm.SETTINGS.get("spelling.languages")
    valor.append("fr")
    assert alm.SETTINGS.get("spelling.languages") == ["es", "en"]


def test_textos_de_reporte_toman_los_del_perfil_activo():
    with alm.ajustes_activos({"documents": {"report_texts": {"header_title": "Informe fotográfico"}}}):
        assert document_texts.report_texts()["header_title"] == "Informe fotográfico"
    assert document_texts.report_texts()["header_title"] == "Reporte fotográfico"


def test_cache_dir_se_crea_al_pedirla_y_respeta_la_raiz(tmp_path):
    raiz = tmp_path / "cache"
    alm.configurar_cache(raiz)
    try:
        assert not raiz.exists()
        carpeta = alm.cache_dir("overpass")
        assert carpeta == raiz / "overpass" and carpeta.is_dir()
        with pytest.raises(ValueError):
            alm.cache_dir("../fuera")
        with pytest.raises(ValueError):
            alm.cache_dir("/absoluta")
    finally:
        alm.configurar_cache(None)


def test_agente_de_usuario_configurable():
    alm.configurar_agente("TresVizo-Herramientas/1.0 (+https://app.tresvizo.com)")
    try:
        assert alm.agente_usuario("x").startswith("TresVizo-Herramientas")
    finally:
        alm.configurar_agente(None)
    assert alm.agente_usuario("x") == "x"


def test_fuentes_incluidas_y_registro_idempotente():
    assert fuentes.REGULAR.is_file() and fuentes.NEGRITA.is_file()
    assert fuentes.registrar_pdf() == (fuentes.PDF_REGULAR, fuentes.PDF_NEGRITA)
    assert fuentes.registrar_pdf() == (fuentes.PDF_REGULAR, fuentes.PDF_NEGRITA)
    from reportlab.pdfbase.pdfmetrics import stringWidth

    # Liberation Sans es métricamente compatible con Arial: "Hola" a 10 pt mide lo que en Arial
    # (H 722 + o 556 + l 222 + a 556 = 2056 milésimas de em).
    assert abs(stringWidth("Hola", fuentes.PDF_REGULAR, 10) - 20.56) < 0.02
    assert fuentes.fuente_pil(20, True).getlength("Hola") > fuentes.fuente_pil(20).getlength("Hola")
