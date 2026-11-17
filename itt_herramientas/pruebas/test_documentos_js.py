"""La sincronización del teléfono (`offline/documentos.js`) y el latido (`offline/compartido.js`) en Node, con una cola y un servidor de
mentira: los casos de la revisión de `web-equipo` (fusión con cambios en vuelo, recuperación con cambios en vuelo, listas por páginas y
documentos que ya no vienen en la lista, latido al volver a la pestaña)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

#: `ITT_OFFLINE_JS` apunta a otra copia de los scripts (p. ej. la de antes de un arreglo, para ver que la prueba falla sin él).
OFFLINE = Path(os.environ.get("ITT_OFFLINE_JS") or Path(__file__).resolve().parents[1] / "static" / "herramientas" / "offline")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")

ARNES = r"""
const copiar = (x) => JSON.parse(JSON.stringify(x));
globalThis.self = globalThis;
Object.defineProperty(globalThis, "navigator", { value: { onLine: true }, configurable: true, writable: true });   // Node trae uno propio, sin onLine
const eventos = [], oyentes = {};
globalThis.document = { dispatchEvent: (e) => eventos.push(e.detail), addEventListener: (t, f) => (oyentes[t] = oyentes[t] || []).push(f),
  getElementById: () => null, querySelectorAll: () => [], documentElement: { dataset: {} }, visibilityState: "visible" };
globalThis.addEventListener = (t, f) => (oyentes["w:" + t] = oyentes["w:" + t] || []).push(f);
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
const almacen = new Map(), ajustes = { cuenta: 1, csrf: "x" };
globalThis.ITT = { cola: {
  async ajuste(k, d) { return k in ajustes ? ajustes[k] : d; }, async guardarAjuste(k, v) { ajustes[k] = v; },
  async borradores() { return [...almacen.values()].map(copiar); }, async borrador(id) { return almacen.has(id) ? copiar(almacen.get(id)) : null; },
  async guardarBorrador(r) { almacen.set(r.id, copiar(r)); },
  async actualizarBorrador(id, fn) { if (!almacen.has(id)) return null; const r = fn(copiar(almacen.get(id))); if (r === null) { almacen.delete(id); return null; } almacen.set(id, copiar(r)); return r; },
  async quitarBorrador(id) { almacen.delete(id); }, uuid() { return "u" + Math.random(); } } };
const peticiones = [];
let servidor = async () => ({ status: 500, body: {} });
globalThis.fetch = async (ruta, o) => {
  o = o || {};
  peticiones.push({ ruta, metodo: o.method || "GET", cuerpo: o.body ? JSON.parse(o.body) : null });
  const r = await servidor(ruta, o);
  return { ok: r.status < 400, status: r.status, type: "basic", async json() { return r.body; }, clone() { return { async text() { return JSON.stringify(r.body); } }; }, async text() { return JSON.stringify(r.body); } };
};
require(process.argv[1] + "/fusion.js");
require(process.argv[1] + "/documentos.js");
const M = "00000000-0000-0000-0000-000000000000";
const docs = ITT.crearDocumentos({ tipo: "cotizacion", lista: "cotizaciones", sintetico: (f) => ({ quote_number: f.folio || "", items: [] }) });
docs.iniciar({ sincronizar: "/s/", lista: "/l/", detalle: `/d/${M}/` });
const fila = (id, revision) => ({ id, revision, actualizada: "2026-11-16T10:00:00Z" });
"""


def correr(caso: str):
    proceso = subprocess.run(["node", "-e", ARNES + "\n(async () => {\n" + caso + "\n})().catch((e) => { console.error(e); process.exit(1); });", str(OFFLINE)],
                             capture_output=True, text=True, timeout=60)
    assert proceso.returncode == 0, proceso.stderr
    return json.loads(proceso.stdout.strip().splitlines()[-1])


# Un servidor de mentira con las mismas reglas que el de verdad: aplica si la base es la vigente, fusiona con la base de su historial si
# no, y si choca guarda copia en conflicto. Lleva la cuenta de las peticiones para medir cuántas hace cada sincronización.
SERVIDOR = r"""
  const srv = { rev: 1, datos: {}, historia: {}, copias: 0, filas: () => [] };
  const guardar = (d) => { srv.rev += 1; srv.datos = d; srv.historia[srv.rev] = d; };
  const sincronizar = (cuerpo) => {
    const { revision_base: base, datos } = cuerpo;
    if (JSON.stringify(datos) === JSON.stringify(srv.datos)) return { resultado: "sin_cambios", servidor_id: "S", revision: srv.rev };
    if (base === srv.rev) { guardar(datos); return { resultado: "actualizada", servidor_id: "S", revision: srv.rev }; }
    try { const f = ITT.fusionar(srv.historia[base], srv.datos, datos, {}); guardar(f); return { resultado: "fusionada", servidor_id: "S", revision: srv.rev, datos: f }; }
    catch (e) { srv.copias += 1; return { resultado: "copia", servidor_id: "C" + srv.copias, revision: 1, copia_de: "S" }; }
  };
"""


def test_escribir_durante_una_fusion_no_crea_copia_en_conflicto():
    """La oficina cambió X; el teléfono manda Y=y1, recibe «fusionada» y mientras viajaba la petición se escribió Y=y2. La siguiente
    subida no debe chocar consigo misma: queda X de la oficina e y2 del teléfono, sin copia."""
    r = correr(SERVIDOR + r"""
      srv.historia[1] = { x: "x0", y: "y0" };
      srv.datos = { x: "x0", y: "y0" };
      guardar({ x: "oficina", y: "y0" });                                     // revisión 2: la oficina cambió X
      almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { x: "x0", y: "y1" }, sucio: true, actualizada: 100 });
      let vez = 0;
      servidor = async (ruta, o) => {
        if (ruta === "/s/") {
          const cuerpo = JSON.parse(o.body);
          const respuesta = sincronizar(cuerpo);
          if (++vez === 1) await ITT.cola.actualizarBorrador("L", (x) => Object.assign(x, { datos: { x: "x0", y: "y2" }, actualizada: 200 }));
          return { status: 200, body: respuesta };
        }
        if (ruta.startsWith("/l/")) return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 5, mas: false } };
        return { status: 404, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify({ srv: { datos: srv.datos, copias: srv.copias }, local: almacen.get("L"),
        resultados: peticiones.filter((p) => p.ruta === "/s/").map((p) => p.cuerpo.revision_base) }));
    """)
    assert r["srv"]["copias"] == 0 and r["srv"]["datos"] == {"x": "oficina", "y": "y2"}
    assert r["local"]["datos"] == {"x": "oficina", "y": "y2"} and r["local"]["sucio"] is False
    assert r["resultados"] == [1, 3]                                         # la segunda parte de la revisión fusionada


def test_recuperacion_con_cambios_en_vuelo_sube_lo_ultimo_antes_de_volver_a_la_version_vigente():
    r = correr(r"""
      almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { a: 1 }, sucio: true, actualizada: 100, sesion: "pagina-0001" });
      let vueltas = 0;
      servidor = async (ruta) => {
        if (ruta === "/s/") {
          vueltas++;
          if (vueltas === 1) await ITT.cola.actualizarBorrador("L", (x) => Object.assign(x, { datos: { a: 2 }, actualizada: 200 }));
          return { status: 200, body: { resultado: "recuperacion", servidor_id: "S", revision: 1, quien: "Beto" } };
        }
        if (ruta === "/l/") return { status: 200, body: { cotizaciones: [fila("S", 1)], siguiente: null } };
        return { status: 200, body: Object.assign(fila("S", 1), { datos: { a: 0 }, recuperaciones: [{ id: 1 }] }) };
      };
      await docs.sincronizar();
      const enviados = peticiones.filter((p) => p.ruta === "/s/").map((p) => [p.cuerpo.datos, p.cuerpo.sesion]);
      console.log(JSON.stringify({ enviados, local: almacen.get("L") }));
    """)
    assert r["enviados"] == [[{"a": 1}, "pagina-0001"], [{"a": 2}, "pagina-0001"]]       # lo escrito durante la petición también llegó
    assert r["local"]["datos"] == {"a": 0} and r["local"]["sucio"] is False


def test_copia_en_conflicto_de_un_original_en_la_papelera_o_borrado_deja_solo_la_copia():
    for copia_de, estado in (("S", 410), (None, 404)):
        r = correr(r"""
          almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { a: 1 }, sucio: true, actualizada: 100 });
          servidor = async (ruta) => {
            if (ruta === "/s/") return { status: 200, body: { resultado: "copia", servidor_id: "C", revision: 1, copia_de: COPIA_DE, conflicto: "Copia en conflicto de algo" } };
            if (ruta === "/d/C/") return { status: 200, body: Object.assign(fila("C", 1), { datos: { a: 1 } }) };
            if (ruta === "/d/S/") return { status: ESTADO, body: { error: "fuera", eliminado: ESTADO === 410 } };
            if (ruta.startsWith("/l/")) return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 9, mas: false } };
            return { status: 404, body: {} };
          };
          await docs.sincronizar();
          const subidas = peticiones.filter((p) => p.ruta === "/s/").length;
          await docs.sincronizar();                                         // y no se vuelve a mandar en cada ciclo
          console.log(JSON.stringify({ ids: [...almacen.keys()].sort(), C: almacen.get("C"), subidas, despues: peticiones.filter((p) => p.ruta === "/s/").length }));
        """.replace("COPIA_DE", json.dumps(copia_de)).replace("ESTADO", str(estado)))
        assert r["ids"] == ["C"] and r["C"]["sucio"] is False, (copia_de, r)
        assert r["subidas"] == 1 and r["despues"] == 1


def test_sincronizar_otra_vez_sin_cambios_es_una_sola_peticion_y_lo_de_la_papelera_se_va():
    r = correr(r"""
      const filas = [1, 2, 3, 4, 5].map((n) => Object.assign(fila("S" + n, 1), { folio: "COT-2026-000" + n, cliente: "C" + n, proyecto: "P" + n, total: n }));
      servidor = async (ruta) => {
        if (ruta === "/l/?desde=0") return { status: 200, body: { cotizaciones: filas, eliminados: [], cursor: 5, mas: false } };
        if (ruta === "/l/?desde=5") return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 5, mas: false } };
        if (ruta === "/l/?desde=5b") return { status: 200, body: {} };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();                                             // teléfono nuevo: resúmenes, ningún detalle
      const primera = peticiones.map((p) => p.ruta);
      peticiones.length = 0;
      await docs.sincronizar();
      const segunda = peticiones.map((p) => p.ruta);
      // Alguien manda S2 a la papelera.
      servidor = async (ruta) => (ruta === "/l/?desde=5" ? { status: 200, body: { cotizaciones: [], eliminados: ["S2"], cursor: 6, mas: false } } : { status: 500, body: {} });
      peticiones.length = 0;
      await docs.sincronizar();
      console.log(JSON.stringify({ primera, segunda, tercera: peticiones.map((p) => p.ruta), ids: [...almacen.keys()].sort(),
        s1: almacen.get("S1"), cursor: await ITT.cola.ajuste("cursor:cotizacion:1", null) }));
    """)
    assert r["primera"] == ["/l/?desde=0"]                                   # ni un solo detalle de toda la empresa
    assert r["segunda"] == ["/l/?desde=5"]                                   # sin cambios: una sola petición
    assert r["ids"] == ["S1", "S3", "S4", "S5"] and r["cursor"] == 6
    assert r["s1"]["parcial"] is True and r["s1"]["datos"]["quote_number"] == "COT-2026-0001" and r["s1"]["resumen"]["total"] == 1


def test_lo_editado_aqui_que_otro_manda_a_la_papelera_se_conserva_para_la_copia():
    r = correr(r"""
      almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { a: 2 }, sucio: true, actualizada: 100 });
      servidor = async (ruta) => {
        if (ruta === "/s/") return { status: 503, body: { error: "ocupado" } };
        if (ruta.startsWith("/l/")) return { status: 200, body: { cotizaciones: [], eliminados: ["S"], cursor: 3, mas: false } };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify(almacen.get("L")));
    """)
    assert r["servidor_id"] == "S" and r["sucio"] is True and r["eliminado_en_servidor"] is True


def test_abrir_uno_del_equipo_trae_sus_datos_y_desde_ahi_se_mantiene():
    r = correr(r"""
      let detalles = 0;
      servidor = async (ruta) => {
        if (ruta === "/l/?desde=0") return { status: 200, body: { cotizaciones: [fila("S", 1)], eliminados: [], cursor: 1, mas: false } };
        if (ruta === "/d/S/") return { status: 200, body: Object.assign(fila("S", detalles++ ? 2 : 1), { datos: { a: detalles } }) };
        if (ruta === "/l/?desde=1") return { status: 200, body: { cotizaciones: [fila("S", 2)], eliminados: [], cursor: 2, mas: false } };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();
      const abierto = await docs.abrir("S");
      const datosAbierto = abierto.datos;
      peticiones.length = 0;
      await docs.sincronizar();                                             // ya se guarda en el teléfono: un cambio trae sus datos
      console.log(JSON.stringify({ abierto, rutas: peticiones.map((p) => p.ruta) }));
    """)
    assert r["abierto"]["parcial"] is False and r["abierto"]["datos"] == {"a": 1} and r["abierto"]["revision_base"] == 1
    assert r["rutas"] == ["/l/?desde=1", "/d/S/"]


def test_al_volver_a_la_pestana_se_late_en_seguida():
    proceso = subprocess.run(["node", "-e", ARNES + r"""
      (async () => {
        const nodo = () => ({ append() {}, set hidden(v) {}, dataset: {} });
        ITT.ui = { h: () => nodo(), limpiar() {}, icono: () => nodo(), boton: () => nodo(), confirmar: async () => true, fechaCorta: () => "" };
        require(process.argv[1] + "/compartido.js");
        const llamadas = [];
        const falso = { urls: { bloqueo: `/b/${M}/` }, pedir: async (ruta, o) => { llamadas.push((o && o.json && o.json.accion) || "GET"); return { mio: true, revision: 1, recuperaciones: [] }; },
          refrescar: async () => null, sincronizar: async () => {} };
        const comp = ITT.compartido.crear({ tipo: "cotizacion", prefijo: "cot", docs: falso, articulo: "la", nombre: "cotización", resumen: () => [] });
        await comp.abrir({ id: "L", servidor_id: "S", recuperaciones: [] }, { latido: 15 });
        const antes = llamadas.filter((a) => a !== "GET").length;
        for (const f of oyentes.visibilitychange || []) f();
        await new Promise((r) => setTimeout(r, 50));
        const despues = llamadas.filter((a) => a !== "GET").length;
        comp.cerrar();
        console.log(JSON.stringify({ antes, despues }));
        process.exit(0);
      })().catch((e) => { console.error(e); process.exit(1); });
    """, str(OFFLINE)], capture_output=True, text=True, timeout=60)
    assert proceso.returncode == 0, proceso.stderr
    r = json.loads(proceso.stdout.strip().splitlines()[-1])
    assert r["despues"] == r["antes"] + 1


def test_tras_perder_el_control_lo_escrito_sigue_con_la_sesion_de_la_pagina():
    proceso = subprocess.run(["node", "-e", ARNES + r"""
      (async () => {
        const nodo = () => ({ append() {}, set hidden(v) {}, dataset: {} });
        ITT.ui = { h: () => nodo(), limpiar() {}, icono: () => nodo(), boton: () => nodo(), confirmar: async () => true, fechaCorta: () => "" };
        require(process.argv[1] + "/compartido.js");
        let respuesta = { mio: true, revision: 1 };
        const falso = { urls: { bloqueo: `/b/${M}/` }, pedir: async () => Object.assign({ recuperaciones: [] }, respuesta), refrescar: async () => null, sincronizar: async () => {} };
        const comp = ITT.compartido.crear({ tipo: "cotizacion", prefijo: "cot", docs: falso, articulo: "la", nombre: "cotización", resumen: () => [] });
        await comp.abrir({ id: "L", servidor_id: "S", recuperaciones: [] }, { latido: 15 });
        const conBloqueo = comp.sesion;
        respuesta = { mio: false, perdido: true, quien: "Beto", revision: 1 };
        for (const f of oyentes.visibilitychange || []) f();                // el siguiente latido dice que lo perdió
        await new Promise((r) => setTimeout(r, 50));
        const despues = comp.sesion;
        comp.cerrar();
        console.log(JSON.stringify({ conBloqueo, despues, cerrado: comp.sesion }));
        process.exit(0);
      })().catch((e) => { console.error(e); process.exit(1); });
    """, str(OFFLINE)], capture_output=True, text=True, timeout=60)
    assert proceso.returncode == 0, proceso.stderr
    r = json.loads(proceso.stdout.strip().splitlines()[-1])
    assert r["conBloqueo"] and r["despues"] == r["conBloqueo"] and r["cerrado"] == ""


def test_la_fusion_del_telefono_es_la_misma_del_servidor():
    """Misma regla en los dos lados (`offline/fusion.js` y `comun.sincronizacion.fusionar`): miles de casos al azar, mismo resultado o
    el mismo choque."""
    import random

    from itt_herramientas.comun.sincronizacion import fusionar

    azar = random.Random(20261116)
    valores = ["a", "b", "c", 1, 2, None, [1], [2], {"x": 1}, {"x": 2}]

    def dic(base=None):
        d = dict(base or {})
        for k in azar.sample(["t", "u", "v", "w"], azar.randint(0, 4)):
            if azar.random() < 0.25:
                d.pop(k, None)
            else:
                d[k] = azar.choice(valores) if azar.random() < 0.8 else dic(d.get(k) if isinstance(d.get(k), dict) else None)
        return d

    def fotos(base=None):
        lista = [dict(f) for f in (base or [])]
        if lista and azar.random() < 0.3:
            lista.pop(azar.randrange(len(lista)))
        if azar.random() < 0.4:
            lista.append({"sha256": azar.choice("pqrstu"), "nota": azar.choice("xyz")})
        if lista and azar.random() < 0.3:
            lista[azar.randrange(len(lista))]["nota"] = azar.choice("xyz")
        if len(lista) > 1 and azar.random() < 0.2:
            azar.shuffle(lista)
        vistos, unicos = set(), []
        for f in lista:
            if f["sha256"] not in vistos:
                vistos.add(f["sha256"])
                unicos.append(f)
        return unicos

    casos = []
    for _ in range(3000):
        base = dic()
        base["fotos"] = fotos()
        actual, nuevos = dic(base), dic(base)
        actual["fotos"], nuevos["fotos"] = fotos(base["fotos"]), fotos(base["fotos"])
        casos.append([base, actual, nuevos])
    claves = {"fotos": "sha256"}
    esperado = []
    for base, actual, nuevos in casos:
        try:
            esperado.append({"ok": fusionar(base, actual, nuevos, claves)})
        except ValueError:
            esperado.append({"choque": True})
    script = r"""
      const { fusionar, ChoqueDeFusion } = require(process.argv[1] + "/fusion.js");
      const casos = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(JSON.stringify(casos.map(([b, a, n]) => { try { return { ok: fusionar(b, a, n, { fotos: "sha256" }) }; } catch (e) { if (e instanceof ChoqueDeFusion) return { choque: true }; throw e; } })));
    """
    proceso = subprocess.run(["node", "-e", script, str(OFFLINE)], input=json.dumps(casos), capture_output=True, text=True, timeout=120)
    assert proceso.returncode == 0, proceso.stderr
    js = json.loads(proceso.stdout)
    diferencias = [(c, e, j) for c, e, j in zip(casos, esperado, js) if e != j]
    assert not diferencias, f"{len(diferencias)} diferencias; la primera: {diferencias[0]}"
    assert sum(1 for e in esperado if "choque" in e) > 100 and sum(1 for e in esperado if "ok" in e) > 100      # hay de los dos


def test_la_base_de_la_fusion_es_lo_enviado_como_lo_guardo_el_servidor():
    """Se mandó «y1 » (con espacio); el servidor lo guarda sin él y fusiona con lo de la oficina; mientras tanto se escribió «y12». Con
    lo enviado tal cual como base, «y1 » ≠ «y1» parecería un choque y saldría una copia en conflicto innecesaria."""
    r = correr(SERVIDOR + r"""
      const limpio = (d) => Object.fromEntries(Object.entries(d).map(([k, v]) => [k, typeof v === "string" ? v.trim() : v]));
      srv.historia[1] = { x: "x0", y: "y0" }; srv.datos = { x: "x0", y: "y0" };
      guardar({ x: "oficina", y: "y0" });
      almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { x: "x0", y: "y1 " }, sucio: true, actualizada: 100 });
      let vez = 0;
      servidor = async (ruta, o) => {
        if (ruta === "/s/") {
          const cuerpo = JSON.parse(o.body);
          cuerpo.datos = limpio(cuerpo.datos);                              // el servidor normaliza lo que recibe
          const respuesta = sincronizar(cuerpo);
          if (respuesta.resultado === "fusionada") respuesta.enviados = cuerpo.datos;
          if (++vez === 1) await ITT.cola.actualizarBorrador("L", (x) => Object.assign(x, { datos: { x: "x0", y: "y12" }, actualizada: 200 }));
          return { status: 200, body: respuesta };
        }
        if (ruta.startsWith("/l/")) return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 5, mas: false } };
        return { status: 404, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify({ copias: srv.copias, datos: srv.datos }));
    """)
    assert r["copias"] == 0 and r["datos"] == {"x": "oficina", "y": "y12"}


def test_guardar_no_acepta_un_documento_que_solo_se_conoce_por_su_resumen():
    r = correr(r"""
      almacen.set("P", { id: "P", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 3, parcial: true, datos: { quote_number: "COT-1" }, sucio: false, actualizada: 1 });
      let error = null;
      try { await docs.guardar("P", { quote_number: "COT-1", items: [] }); } catch (e) { error = e.message; }
      console.log(JSON.stringify({ error, P: almacen.get("P") }));
    """)
    assert r["error"] and r["P"]["sucio"] is False and r["P"]["datos"] == {"quote_number": "COT-1"}


def test_un_bloqueo_que_ya_caduco_no_se_muestra_en_la_lista():
    r = correr(r"""
      require(process.argv[1] + "/ayudas.js");
      const ahora = Date.parse("2026-11-16T12:00:00Z");
      const con = (caduca, mio) => ({ bloqueo: { nombre: "Beto", caduca, mio: !!mio } });
      console.log(JSON.stringify([ITT.ui.bloqueoVigente(con("2026-11-16T12:01:00Z"), ahora), ITT.ui.bloqueoVigente(con("2026-11-16T11:59:00Z"), ahora),
        ITT.ui.bloqueoVigente(con("2026-11-16T12:01:00Z", true), ahora), ITT.ui.bloqueoVigente({ bloqueo: null }, ahora)]));
    """)
    assert r == [True, False, False, False]
