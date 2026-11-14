"""PWA de Herramientas en un navegador móvil real (motor Chromium y WebKit): captura sin conexión, cierre forzado y
reapertura, subida reanudable, confirmación por sha256 y avisos. Cada prueba corre en los dos motores."""
from __future__ import annotations

import hashlib
import os
import json
import re
from pathlib import Path

import pytest
import time

from playwright.sync_api import expect

from itt_herramientas.comun.perfiles import raiz_archivos
from itt_herramientas.models import Captura
from tvplataforma.models import Subida

from .conftest import foto

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
TIEMPO = 20000


def esperar(pagina, expresion: str, timeout=TIEMPO):
    """Espera a que `expresion` (JS, puede devolver una promesa) sea verdadera. `wait_for_function` usa eval y la CSP del
    portal lo prohíbe (con razón), así que se consulta con `evaluate`."""
    limite = time.monotonic() + timeout / 1000
    while True:
        try:
            if pagina.evaluate(expresion):
                return
        except Exception as exc:   # la página puede estar recargándose
            if "context" not in str(exc).lower() and "navigat" not in str(exc).lower():
                raise
        if time.monotonic() > limite:
            try:
                pista = pagina.evaluate("(document.querySelector('#itt-mensaje') || {}).textContent || ''")
            except Exception:
                pista = ""
            try:
                estado = pagina.evaluate("ITT.cola.resumen().then(r => JSON.stringify(r.items.map(i => [i.estado, i.error, i.intentos, !!i.uploadUrl])))")
            except Exception:
                estado = "?"
            raise AssertionError(f"Tiempo agotado esperando: {expresion} (mensaje de la página: {pista!r}; cola: {estado})")
        time.sleep(0.1)


@pytest.fixture
def usuaria(crear_usuario, roles):
    u = crear_usuario("ana")
    roles.conceder(u, "herramientas", "reportes")
    return u


def contexto(motor, tmp_path, servidor, sesion, **extra):
    """Contexto con perfil de teléfono, con la sesión ya iniciada. `perfil` persistente = el navegador se puede cerrar y reabrir."""
    opciones = {k: v for k, v in motor["dispositivo"].items() if k != "default_browser_type"}
    opciones.update(extra)
    perfil = tmp_path / f"perfil-{motor['nombre']}"
    ctx = motor["tipo"].launch_persistent_context(str(perfil), headless=True, **opciones)
    ctx.add_cookies([sesion])
    return ctx


def abrir(ctx, servidor, ruta="/capturar/"):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("request", lambda r: print("REQ", r.method, r.url[-60:]))
        pagina.on("response", lambda r: print("RES", r.status, r.request.method, r.url[-60:]))
        pagina.on("requestfailed", lambda r: print("FAIL", r.method, r.url[-60:], r.failure))
        pagina.on("console", lambda m: print("CON", m.type, m.text[:200]))
    pagina.goto(servidor.url + ruta)
    pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
    pagina.evaluate("() => { ITT.tus.tiempos.corto = 4000; ITT.tus.tiempos.trozo = 8000; }")    # plazos cortos: una petición colgada no alarga la prueba
    return pagina


def con_service_worker(pagina):
    """Espera a que el service worker controle la página (así lo que sigue ya funciona sin red)."""
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    if not pagina.evaluate("!!navigator.serviceWorker.controller"):
        pagina.reload()
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        esperar(pagina, "!!navigator.serviceWorker.controller", timeout=TIEMPO)


# El navegador de Playwright no siempre deja interceptar con page.route las peticiones de una página controlada por un service
# worker (WebKit). La red que falla se simula desde la propia página, igual en los dos motores: se reemplaza window.fetch.
_INSTALAR = """(regla) => {
    if (!window.__fetch0) window.__fetch0 = window.fetch.bind(window);
    const f0 = window.__fetch0;
    const sub = /\\/subidas\\/[0-9a-f-]{36}\\/$/;
    window.fetch = async (url, o) => {
        const u = String((url && url.url) || url);
        const m = (((o && o.method) || 'GET')).toUpperCase();
        if (regla === 'cortar_datos' && m === 'PATCH' && sub.test(u)) throw new TypeError('Failed to fetch');
        if (regla === 'colgar_datos' && m === 'PATCH' && sub.test(u)) return new Promise((_, rechazar) => o.signal.addEventListener('abort', () => rechazar(new DOMException('abort', 'AbortError'))));
        if (regla === 'error_503' && m === 'PATCH' && sub.test(u)) return new Response('{}', {status: 503});
        if (regla === 'sha_alterado' && m === 'GET' && sub.test(u)) {
            const r = await f0(url, o); const j = await r.json(); j.sha256 = '0'.repeat(64);
            return new Response(JSON.stringify(j), {status: r.status, headers: {'Content-Type': 'application/json'}});
        }
        if (regla === 'todo_subidas' && /\\/subidas\\//.test(u)) throw new TypeError('Failed to fetch');
        return f0(url, o);
    };
}"""


def fallar(pagina, regla: str):
    """Activa una falla de red simulada hasta recargar la página (`regla`: cortar_datos, colgar_datos, error_503, sha_alterado, todo_subidas)."""
    pagina.evaluate(_INSTALAR, regla)


def sin_fallas(pagina):
    pagina.evaluate("() => { if (window.__fetch0) window.fetch = window.__fetch0; }")


def banda_visible(pagina) -> bool:
    return pagina.locator("#itt-pendientes").is_visible()


def texto_contador(pagina) -> str:
    return pagina.locator("#itt-pendientes [data-itt=texto]").inner_text()


def cola(pagina) -> dict:
    return pagina.evaluate("ITT.cola.resumen()")


def capturar(pagina, *archivos: bytes, camara=True):
    """La cámara entrega una foto por toma; la galería permite varias a la vez."""
    def entregar(selector, lote):
        pagina.set_input_files(selector, [{"name": f"IMG_{i}.jpg", "mimeType": "image/jpeg", "buffer": d} for i, d in lote])
    base = int(pagina.evaluate("document.documentElement.dataset.guardadas || '0'"))
    if camara:
        for n, datos in enumerate(archivos, 1):
            entregar("#itt-camara", [(n, datos)])
            esperar(pagina, f"Number(document.documentElement.dataset.guardadas || 0) >= {base + n}", timeout=TIEMPO)
    else:
        entregar("#itt-galeria", list(enumerate(archivos, 1)))
        esperar(pagina, f"Number(document.documentElement.dataset.guardadas || 0) >= {base + len(archivos)}", timeout=TIEMPO)


def esperar_guardadas(pagina, n):
    esperar(pagina, f"document.querySelector('#itt-pendientes').dataset.total === '{n}'", timeout=TIEMPO)


def empujar_hasta(pagina, expresion: str, timeout=TIEMPO * 2):
    """Como la app (cada 30 s y al volver la señal), empuja la subida cada par de segundos hasta que `expresion` sea cierta."""
    limite = time.monotonic() + timeout / 1000
    while time.monotonic() < limite:
        if pagina.evaluate(expresion):
            return
        pagina.evaluate("ITT.subirAhora()")
        time.sleep(1.5)
    esperar(pagina, expresion, timeout=1000)


def esperar_sin_pendientes(pagina, timeout=TIEMPO * 2):
    """Espera a que la cola quede vacía, empujando la subida cada par de segundos (la app lo hace cada 30 s y al volver la señal)."""
    limite = time.monotonic() + timeout / 1000
    while time.monotonic() < limite:
        if pagina.evaluate("document.querySelector('#itt-pendientes').dataset.total === '0'"):
            return
        pagina.evaluate("ITT.subirAhora()")
        time.sleep(1.5)
    esperar(pagina, "document.querySelector('#itt-pendientes').dataset.total === '0'", timeout=1000)


def test_modo_avion_captura_y_sube_al_volver_la_senal(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria), geolocation={"latitude": 28.633, "longitude": -106.069}, permissions=["geolocation"])
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        ctx.set_offline(True)                                          # modo avión
        pagina.reload()                                                # la app arranca desde el service worker, sin red
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        expect(pagina.locator("#itt-red")).to_contain_text("Sin conexión")
        pagina.locator("#itt-ubicacion").click()
        expect(pagina.locator("#itt-geo")).to_contain_text("Ubicación activa", timeout=TIEMPO)
        originales = [foto(color="#2F6F9F"), foto(color="#9F6F2F"), foto(color="#4F9F2F", gps=False)]
        capturar(pagina, *originales)
        esperar_guardadas(pagina, "3")
        assert "3 pendientes" in texto_contador(pagina)
        assert Captura.objects.count() == 0
        r = cola(pagina)
        assert r["total"] == 3 and all(i["bytes"] < len(originales[0]) for i in r["items"])        # reducidas antes de guardar
        assert all(i["estado"] == "en_cola" for i in r["items"])
        ctx.set_offline(False)                                         # vuelve la señal
        pagina.evaluate("ITT.subirAhora()")
        esperar_sin_pendientes(pagina)
        assert not banda_visible(pagina)                                 # sin pendientes la banda no ocupa lugar (UX-ITT-05)
        assert pagina.evaluate("document.querySelector('#itt-pendientes').dataset.total") == "0"
        capturas = list(Captura.objects.order_by("orden"))
        assert len(capturas) == 3
        assert [c.sin_ubicacion for c in capturas] == [False, False, False]
        assert capturas[2].lat == pytest.approx(28.633, abs=1e-3) and capturas[2].precision is not None     # sin EXIF: la ubicación sale de la Geolocation API
        assert capturas[0].lat == pytest.approx(28.633, abs=1e-3) and capturas[0].lon == pytest.approx(-106.0691, abs=1e-3)   # del EXIF
        assert capturas[0].tomada == "2026-11-13T09:30:00"
        for c in capturas:                                             # lo guardado en el servidor es exactamente lo que se capturó
            archivo = (raiz_archivos() / c.ruta).read_bytes()
            assert hashlib.sha256(archivo).hexdigest() == c.sha256 and max(c.ancho, c.alto) <= 2400
        assert cola(pagina)["total"] == 0
    finally:
        ctx.close()


def test_cierre_forzado_y_reapertura_no_pierde_capturas(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    pagina = abrir(ctx, servidor)
    con_service_worker(pagina)
    ctx.set_offline(True)
    capturar(pagina, foto(color="#112233"), foto(color="#332211"))
    esperar_guardadas(pagina, "2")
    ids = [i["id"] for i in cola(pagina)["items"]]
    ctx.close()                                                        # cierre del navegador sin avisar

    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))     # mismo perfil: lo guardado en el teléfono sigue ahí
    try:
        ctx.set_offline(True)                                          # y sigue sin señal
        pagina = abrir(ctx, servidor)
        assert [i["id"] for i in cola(pagina)["items"]] == ids
        assert "2 pendientes" in texto_contador(pagina)
        assert pagina.locator("#itt-lista li").count() == 2
        ctx.set_offline(False)                                           # vuelve la señal: la persona abre la app otra vez
        pagina.reload()
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 2
    finally:
        ctx.close()


def test_corte_a_mitad_de_la_subida_se_reanuda_sin_duplicar(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        # La subida se crea en el servidor, pero los datos nunca llegan (se cae la red a media subida).
        fallar(pagina, "cortar_datos")
        capturar(pagina, foto(color="#445566"))
        esperar_guardadas(pagina, "1")
        pagina.evaluate("ITT.subirAhora()")
        esperar(pagina, "ITT.cola.resumen().then(r => r.items[0] && r.items[0].error)", timeout=TIEMPO)
        item = cola(pagina)["items"][0]
        assert item["uploadUrl"] and Subida.objects.filter(terminada=False).count() == 1 and Captura.objects.count() == 0
        ctx.close()                                                    # se cierra todo con la subida a medias

        ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
        ctx.set_offline(True)                                          # la app sube sola al abrir: sin red, para poder mirar la cola antes de que lo haga
        pagina = abrir(ctx, servidor)
        assert cola(pagina)["items"][0]["uploadUrl"] == item["uploadUrl"]          # recuerda dónde iba
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1
        assert Subida.objects.filter(terminada=False).count() == 0                  # reanudó la misma subida, no dejó otra colgada
    finally:
        ctx.close()


def test_no_borra_hasta_que_el_servidor_confirma_el_mismo_sha256(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)

        fallar(pagina, "sha_alterado")                                   # la confirmación llega con OTRO sha256: la captura debe quedarse en el teléfono
        capturar(pagina, foto(color="#778899"))
        esperar_guardadas(pagina, "1")
        pagina.evaluate("ITT.subirAhora()")
        esperar(pagina, "ITT.cola.resumen().then(r => r.items[0] && /sha256/.test(r.items[0].error))", timeout=TIEMPO)
        assert cola(pagina)["total"] == 1                              # NO se borró
        assert Captura.objects.count() == 1                            # el servidor sí la recibió...
        sin_fallas(pagina)
        pagina.evaluate("ITT.subirAhora()")                            # ...y al confirmar bien (misma foto, mismo sha) se limpia
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1                            # sin duplicados
    finally:
        ctx.close()


def test_una_conexion_colgada_no_bloquea_la_cola(motor, tmp_path, servidor, sesion_de, usuaria):
    """Una conexión que se cae sin avisar deja la petición esperando; la cola debe soltarla por plazo y reintentar."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.evaluate("() => { ITT.tus.tiempos.corto = 3000; ITT.tus.tiempos.trozo = 3000; }")
        fallar(pagina, "colgar_datos")
        capturar(pagina, foto(color="#0f0f0f"))
        empujar_hasta(pagina, "ITT.cola.resumen().then(r => !!r.items[0] && r.items[0].intentos >= 2)", timeout=40000)     # soltó la petición colgada y volvió a intentar
        assert cola(pagina)["total"] == 1 and Captura.objects.count() == 0
        sin_fallas(pagina)
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1
    finally:
        ctx.close()


def test_un_error_del_servidor_deja_la_captura_en_el_telefono(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        fallar(pagina, "error_503")
        capturar(pagina, foto(color="#990011"))
        esperar_guardadas(pagina, "1")
        pagina.evaluate("ITT.subirAhora()")
        esperar(pagina, "ITT.cola.resumen().then(r => r.items[0] && r.items[0].intentos >= 1 && r.items[0].estado === 'en_cola')", timeout=TIEMPO)
        assert cola(pagina)["total"] == 1
        sin_fallas(pagina)
        pagina.evaluate("ITT.subirAhora()")
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1
    finally:
        ctx.close()


def test_archivo_que_el_servidor_rechaza_no_se_pierde_ni_se_reintenta_sin_fin(motor, tmp_path, servidor, sesion_de, usuaria, settings):
    settings.TV_HERRAMIENTAS_CUOTA = 10                              # el servidor rechaza todo por falta de espacio
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        capturar(pagina, foto(color="#123456"))
        esperar_guardadas(pagina, "1")
        empujar_hasta(pagina, "ITT.cola.resumen().then(r => !!r.items[0] && r.items[0].estado === 'error')")
        item = cola(pagina)["items"][0]
        assert "espacio" in item["error"] and item["permanente"] is True
        assert "1 con error" in texto_contador(pagina)
        expect(pagina.locator("#itt-lista li [data-itt=reintentar]")).to_be_visible()
        settings.TV_HERRAMIENTAS_CUOTA = 5 * 1024 ** 3
        pagina.locator("#itt-lista li [data-itt=reintentar]").click()
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1
    finally:
        ctx.close()


def test_sesion_caducada_conserva_la_cola(motor, tmp_path, servidor, sesion_de, usuaria):
    cookie = sesion_de(usuaria)
    ctx = contexto(motor, tmp_path, servidor, cookie)
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.evaluate("ITT.subirAhora()")                            # registra la cuenta y el token
        ctx.clear_cookies()                                            # la sesión caducó
        capturar(pagina, foto(color="#abcdef"))
        esperar_guardadas(pagina, "1")
        pagina.evaluate("ITT.subirAhora()")
        esperar(pagina, "document.querySelector('#itt-pendientes').dataset.estado === 'pausada'", timeout=TIEMPO)
        assert "iniciar sesión" in texto_contador(pagina)
        assert cola(pagina)["total"] == 1 and Captura.objects.count() == 0
        ctx.add_cookies([cookie])                                      # la persona vuelve a entrar
        pagina.evaluate("ITT.subirAhora()")
        esperar_sin_pendientes(pagina)
        assert Captura.objects.count() == 1
    finally:
        ctx.close()


def test_aviso_antes_de_cerrar_solo_con_pendientes(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        ctx.set_offline(True)
        probar = "(() => { const e = new Event('beforeunload', {cancelable: true}); dispatchEvent(e); return e.defaultPrevented; })()"
        assert pagina.evaluate(probar) is False                        # sin pendientes no molesta
        capturar(pagina, foto(color="#654321"))
        esperar_guardadas(pagina, "1")
        assert pagina.evaluate(probar) is True                         # con pendientes pide confirmar
    finally:
        ctx.close()


def test_almacenamiento_persistente_y_espacio(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        esperar(pagina, "document.documentElement.dataset.almacenamientoUso !== undefined", timeout=TIEMPO)
        # Que el navegador CONCEDA persistencia depende de su criterio (instalación, uso); lo que se verifica es que se pide
        # y que se muestra el espacio. La concesión real en iOS/Android se prueba en teléfono (docs/pwa.md).
        assert pagina.evaluate("typeof navigator.storage.persist") == "function"
        assert pagina.evaluate("document.documentElement.dataset.almacenamientoPersistente") in ("true", "false")
        assert 0 <= float(pagina.evaluate("document.documentElement.dataset.almacenamientoUso")) < 0.8
    finally:
        ctx.close()


def test_aviso_de_espacio_al_80_por_ciento(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        pagina.evaluate("navigator.storage.estimate = async () => ({usage: 85, quota: 100})")
        pagina.evaluate("ITT.pedirAlmacenamiento()")
        expect(pagina.locator("#itt-almacenamiento")).to_contain_text("85 %")
    finally:
        ctx.close()


def test_web_share_con_archivo_y_alternativa_de_descarga(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria), accept_downloads=True)
    try:
        pagina = abrir(ctx, servidor)
        compartido = pagina.evaluate("""async () => {
            const llamadas = [];
            navigator.canShare = (d) => !!(d.files && d.files.length);
            navigator.share = async (d) => { llamadas.push({n: d.files.length, nombre: d.files[0].name, tipo: d.files[0].type, tam: d.files[0].size}); };
            const r = await ITT.compartirArchivo(new Blob(['%PDF-1.4 prueba'], {type: 'application/pdf'}), 'Reporte.pdf', {titulo: 'Reporte'});
            return {r, llamadas};
        }""")
        assert compartido == {"r": "compartido", "llamadas": [{"n": 1, "nombre": "Reporte.pdf", "tipo": "application/pdf", "tam": 15}]}
        assert pagina.evaluate("""async () => {
            navigator.share = async () => { const e = new Error('cancelado'); e.name = 'AbortError'; throw e; };
            return ITT.compartirArchivo(new Blob(['x'], {type: 'application/pdf'}), 'a.pdf');
        }""") == "cancelado"
        with pagina.expect_download() as descarga:                     # el navegador no sabe compartir archivos: se descarga
            pagina.evaluate("""() => { navigator.canShare = () => false; return ITT.compartirArchivo(new Blob(['x'], {type: 'application/pdf'}), 'Reporte.pdf'); }""")
        assert descarga.value.suggested_filename == "Reporte.pdf"
    finally:
        ctx.close()


def test_reduccion_en_worker_aplica_orientacion_y_lee_exif(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        cuerpo = list(foto(tam=(4000, 2000), orientacion=6))
        r = pagina.evaluate("""async (bytes) => {
            const archivo = new File([new Uint8Array(bytes)], 'IMG.jpg', {type: 'image/jpeg'});
            const r = await ITT.reducirImagen(archivo);
            const h = new Uint8Array(await crypto.subtle.digest('SHA-256', await r.blob.arrayBuffer()));
            return {via: r.via, ancho: r.ancho, alto: r.alto, tam: r.blob.size, tipo: r.blob.type, exif: r.exif,
                    sha_ok: Array.from(h, (b) => b.toString(16).padStart(2, '0')).join('') === r.sha256};
        }""", cuerpo)
        assert r["via"] == "worker" and r["sha_ok"] and r["tipo"] == "image/jpeg"
        assert (r["ancho"], r["alto"]) == (1200, 2400)                 # lado mayor 2400 y la orientación EXIF (girar 90°) ya aplicada
        assert r["tam"] < len(cuerpo)
        assert r["exif"]["tomada"] == "2026-11-13T09:30:00" and r["exif"]["lat"] == pytest.approx(28.633, abs=1e-3) and r["exif"]["lon"] == pytest.approx(-106.0691, abs=1e-3)
        sin = pagina.evaluate("""async (bytes) => { const r = await ITT.reducirImagen(new File([new Uint8Array(bytes)], 'a.jpg', {type: 'image/jpeg'}), {sinWorker: true}); return {via: r.via, ancho: r.ancho}; }""", cuerpo)
        assert sin["via"] == "hilo" and sin["ancho"] == 1200          # alternativa sin Worker
        malo = pagina.evaluate("""async () => { try { await ITT.reducirImagen(new File([new Uint8Array([1,2,3])], 'x.jpg', {type: 'image/jpeg'})); return 'sin error'; } catch (e) { return 'error'; } }""")
        assert malo == "error"                                         # un archivo que no es imagen no entra a la cola
    finally:
        ctx.close()


def test_archivo_que_no_es_imagen_no_entra_a_la_cola(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        pagina.set_input_files("#itt-galeria", [{"name": "nota.jpg", "mimeType": "image/jpeg", "buffer": b"no soy una foto"}])
        expect(pagina.locator("#itt-mensaje")).to_contain_text("No se pudo guardar")
        assert cola(pagina)["total"] == 0
    finally:
        ctx.close()


def test_instalable_manifiesto_service_worker_y_guia(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.goto(servidor.url + "/")
        pagina.wait_for_selector("#itt-pendientes", state="attached", timeout=TIEMPO)
        enlace = pagina.evaluate("document.querySelector('link[rel=manifest]').href")
        manifiesto = json.loads(pagina.evaluate("fetch(document.querySelector('link[rel=manifest]').href).then(r => r.text())"))
        assert enlace.endswith("/manifest.webmanifest") and manifiesto["scope"] == "/" and manifiesto["start_url"] == "/"
        for icono in manifiesto["icons"]:
            assert pagina.evaluate("(u) => fetch(u).then(r => r.status)", icono["src"]) == 200
        registro = pagina.evaluate("navigator.serviceWorker.getRegistration().then(r => ({scope: r.scope, activo: !!r.active}))")
        assert registro == {"scope": servidor.url + "/", "activo": True}
        assert pagina.evaluate("document.querySelector('meta[name=theme-color]').content") == "#07356F"
        assert pagina.evaluate("!!document.querySelector('link[rel=apple-touch-icon]')")
        pagina.goto(servidor.url + "/instalar/")
        expect(pagina.locator("#guia-iphone")).to_contain_text("Agregar a inicio")
        expect(pagina.locator("#guia-android")).to_contain_text("Instalar app")
        expect(pagina.locator("#itt-estado-instalacion")).to_contain_text("pestaña del navegador")
    finally:
        ctx.close()


def test_nueva_version_se_ofrece_y_no_se_activa_sola(motor, tmp_path, servidor, sesion_de, usuaria, monkeypatch):
    from itt_herramientas.offline import sw

    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        fallar(pagina, "todo_subidas")                                  # la captura queda pendiente mientras llega la versión nueva
        capturar(pagina, foto(color="#0a0a0a"))
        monkeypatch.setattr(sw, "version", lambda: "version-nueva-de-prueba")      # el servidor publica otra versión del service worker
        pagina.evaluate("navigator.serviceWorker.getRegistration().then(r => r.update())")
        expect(pagina.locator("#itt-actualizar")).to_be_visible(timeout=TIEMPO)
        assert pagina.evaluate("!!document.querySelector('html[data-captura-lista]')") and "pendiente" in texto_contador(pagina)   # la página NO se recargó sola
        assert "version-nueva-de-prueba" not in pagina.evaluate("navigator.serviceWorker.controller.scriptURL")
        pagina.locator("#itt-actualizar [data-itt=actualizar]").click()
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)                     # tras tocar «Actualizar» se recarga con la versión nueva
        esperar(pagina, "navigator.serviceWorker.getRegistration().then(r => !!r.active && !r.waiting)")
        esperar_sin_pendientes(pagina)                                   # la captura no se perdió: sigue en el teléfono hasta subirse
        assert Captura.objects.count() == 1
    finally:
        ctx.close()


def test_diseno_para_telefono_sin_desplazamiento_horizontal_y_objetivos_tactiles(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria), viewport={"width": 360, "height": 740})
    try:
        pagina = abrir(ctx, servidor)
        ctx.set_offline(True)
        capturar(pagina, foto(color="#202020"))
        esperar_guardadas(pagina, "1")
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for selector in ("label.tv-boton", "#itt-subir-ahora", "#itt-ubicacion", "#itt-lista button", "#itt-pendientes a"):
            for caja in [e.bounding_box() for e in pagina.locator(selector).all() if e.is_visible()]:
                assert caja["height"] >= 43.5, (selector, caja)
    finally:
        ctx.close()


def test_primera_apertura_sin_conexion_sale_con_estilos(motor, tmp_path, servidor, sesion_de, usuaria):
    """UX-ITT-02: recién instalado el service worker (sin haber vuelto a abrir la página), perder la señal no deja la página en texto plano."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
        # La precarga ya guardó lo compartido, aunque ninguna página controlada lo haya pedido todavía.
        guardadas = pagina.evaluate("caches.keys().then(async (ks) => { const c = await caches.open(ks.find((k) => k.startsWith('itt-herramientas-'))); return (await c.keys()).map((r) => new URL(r.url).pathname); })")
        for archivo in ("tokens.css", "base.css", "componentes.css", "img/marca.png"):
            assert f"/static/tvplataforma/{archivo}" in guardadas, archivo
        ctx.set_offline(True)
        pagina.goto(servidor.url + "/cotizaciones/")
        pagina.wait_for_selector("#cot", timeout=TIEMPO)
        hojas = pagina.evaluate("[...document.querySelectorAll('link[rel=stylesheet]')].filter((l) => !l.href.endsWith('/perfil.css')).map((l) => [new URL(l.href).pathname, !!l.sheet && l.sheet.cssRules.length > 0])")
        assert hojas and all(ok for _, ok in hojas), hojas
        assert pagina.evaluate("getComputedStyle(document.querySelector('.tv-cabecera')).display") != "inline"     # el encabezado lleva su estilo
        assert pagina.evaluate("document.querySelector('.tv-marca img').complete && document.querySelector('.tv-marca img').naturalWidth > 0")
    finally:
        ctx.close()


def test_banda_de_pendientes_solo_cuando_hay_algo_y_solo_donde_sirve(motor, tmp_path, servidor, sesion_de, usuaria):
    """UX-ITT-05 y UX-ITT-14: sin pendientes no hay banda; con pendientes sale en las páginas de campo y en el inicio, no en el resto,
    con el verbo en singular o plural según el número y un enlace a lo que está pendiente."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        assert not banda_visible(pagina)                                        # nada pendiente: no ocupa lugar
        assert pagina.evaluate("document.documentElement.dataset.banda") == "no"
        agregar = "(n) => ITT.cola.agregar({ blob: new Blob([new Uint8Array(10 + n)], { type: 'image/jpeg' }), estado: 'error', error: 'prueba', sha256: String(n).repeat(64).slice(0, 64), meta: { referencia: 'libre' } })"
        pagina.evaluate(agregar, 1)                                              # pendientes que no se suben solos (en error): la banda no depende de la carrera con la subida
        esperar_guardadas(pagina, "1")
        expect(pagina.locator("#itt-pendientes")).to_be_visible()
        assert texto_contador(pagina).startswith("1 pendiente") and "guardado en este navegador" in texto_contador(pagina)
        assert pagina.locator("#itt-pendientes [data-itt=enlace]").is_hidden()   # lo pendiente es de esta página: ya está a la vista
        pagina.evaluate(agregar, 2)
        esperar_guardadas(pagina, "2")
        assert "2 pendientes" in texto_contador(pagina) and "guardados en este navegador" in texto_contador(pagina)
        pagina.goto(servidor.url + "/reportes/")                                # otra página de campo: el enlace lleva a las capturas
        pagina.wait_for_selector("#itt-pendientes", state="attached", timeout=TIEMPO)
        expect(pagina.locator("#itt-pendientes")).to_be_visible(timeout=TIEMPO)
        enlace = pagina.locator("#itt-pendientes [data-itt=enlace]")
        expect(enlace).to_be_visible(timeout=TIEMPO)
        assert enlace.get_attribute("href") == "/capturar/" and enlace.inner_text() == "Ver capturas"
        pagina.goto(servidor.url + "/preferencias/")                            # página que no es de campo: la cola cuenta, la banda no sale
        pagina.wait_for_selector("#itt-pendientes", state="attached", timeout=TIEMPO)
        esperar(pagina, "document.querySelector('#itt-pendientes').dataset.total === '2'", timeout=TIEMPO)
        assert not banda_visible(pagina) and pagina.evaluate("document.documentElement.dataset.banda") == "no"
        pagina.goto(servidor.url + "/")                                         # el inicio sí: es donde se decide qué abrir
        esperar(pagina, "document.querySelector('#itt-pendientes').dataset.total === '2'", timeout=TIEMPO)
        expect(pagina.locator("#itt-pendientes")).to_be_visible(timeout=TIEMPO)
    finally:
        ctx.close()


def test_cabecera_de_las_paginas_de_campo_lleva_al_inicio_y_tiene_cuenta_y_salida(motor, tmp_path, servidor, sesion_de, usuaria):
    """UX-ITT-03: salida clara desde las páginas de campo, también sin conexión (la página sigue siendo la misma para todas las cuentas)."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria), viewport={"width": 360, "height": 740})
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.goto(servidor.url + "/reportes/")
        esperar(pagina, "document.querySelector('#itt-menu-nombre').textContent === 'ana'", timeout=TIEMPO)
        assert pagina.evaluate("document.querySelector('.tv-cabecera__modulo').getAttribute('href')") == "/"
        assert pagina.evaluate("document.querySelector('a.tv-marca').getAttribute('href')") == "/"
        pagina.locator("#itt-menu summary").click()
        panel = pagina.locator("#itt-menu .tv-menu__panel")
        expect(panel).to_be_visible()
        assert pagina.locator("#itt-menu a[data-itt-menu=reportes]").get_attribute("aria-current") == "page"
        assert pagina.locator("#itt-menu a[data-itt-menu=capturar]").is_visible()
        assert pagina.locator("#itt-menu a[data-itt-menu=croquis]").is_hidden()          # la cuenta no tiene ese rol: no se ofrece
        assert pagina.locator("#itt-menu a[data-itt-menu=cuenta]").is_visible() and pagina.locator("#itt-salir-boton").is_visible()
        for caja in [e.bounding_box() for e in panel.locator("a, button").all() if e.is_visible()]:
            assert caja["height"] >= 43.5, caja                                          # objetivos táctiles de 44 px
        # Cerrar sesión pide confirmar y dice qué pasa con lo pendiente.
        with pagina.expect_request(lambda r: r.method == "POST" and r.url.endswith("/sso/salir")) as pedido:
            pagina.locator("#itt-salir-boton").click()
            expect(pagina.locator("#dlg-salir")).to_be_visible()
            assert "iniciar sesión otra vez" in pagina.locator("#dlg-salir-texto").inner_text()
            pagina.locator("#dlg-salir-si").click()
        assert pedido.value.post_data and "csrfmiddlewaretoken=" in pedido.value.post_data
    finally:
        ctx.close()


def test_menu_sin_conexion_recuerda_la_cuenta_y_avisa_que_salir_necesita_senal(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.goto(servidor.url + "/cotizaciones/")
        esperar(pagina, "document.querySelector('#itt-menu-nombre').textContent === 'ana'", timeout=TIEMPO)
        ctx.set_offline(True)
        pagina.goto(servidor.url + "/cotizaciones/")
        pagina.wait_for_selector("#cot", timeout=TIEMPO)
        esperar(pagina, "document.querySelector('#itt-menu-nombre').textContent === 'ana'", timeout=TIEMPO)
        pagina.locator("#itt-menu summary").click()
        assert pagina.locator("#itt-menu a[data-itt-menu=reportes]").is_visible()        # sigue pudiendo moverse entre las herramientas guardadas
        expect(pagina.locator("#itt-salir-aviso")).to_be_visible()
        pagina.locator("#itt-salir-boton").click(force=True)
        assert not pagina.locator("#dlg-salir").is_visible()                              # sin señal no hay cierre de sesión a medias
    finally:
        ctx.close()
