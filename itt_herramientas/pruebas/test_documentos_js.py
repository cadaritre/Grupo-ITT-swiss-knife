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
require(process.argv[1] + "/documentos.js");
const M = "00000000-0000-0000-0000-000000000000";
const docs = ITT.crearDocumentos({ tipo: "cotizacion", lista: "cotizaciones" });
docs.iniciar({ sincronizar: "/s/", lista: "/l/", detalle: `/d/${M}/` });
const fila = (id, revision) => ({ id, revision, actualizada: "2026-11-16T10:00:00Z" });
"""


def correr(caso: str):
    proceso = subprocess.run(["node", "-e", ARNES + "\n(async () => {\n" + caso + "\n})().catch((e) => { console.error(e); process.exit(1); });", str(OFFLINE)],
                             capture_output=True, text=True, timeout=60)
    assert proceso.returncode == 0, proceso.stderr
    return json.loads(proceso.stdout.strip().splitlines()[-1])


def test_tras_una_fusion_con_cambios_en_vuelo_se_conserva_la_base_y_se_vuelve_a_fusionar():
    r = correr(r"""
      almacen.set("L", { id: "L", tipo: "cotizacion", cuenta: 1, servidor_id: "S", revision_base: 1, datos: { a: 1 }, sucio: true, actualizada: 100 });
      let vueltas = 0;
      servidor = async (ruta, o) => {
        if (ruta === "/s/") {
          vueltas++;
          if (vueltas === 1) {                       // mientras viaja la petición, la persona sigue escribiendo
            await ITT.cola.actualizarBorrador("L", (x) => Object.assign(x, { datos: { a: 2 }, actualizada: 200 }));
            return { status: 200, body: { resultado: "fusionada", servidor_id: "S", revision: 3, datos: { a: 1, oficina: 9 } } };
          }
          return { status: 200, body: { resultado: "fusionada", servidor_id: "S", revision: 4, datos: { a: 2, oficina: 9 } } };
        }
        if (ruta === "/l/") return { status: 200, body: { cotizaciones: [fila("S", 4)], siguiente: null } };
        return { status: 200, body: Object.assign(fila("S", 4), { datos: { a: 2, oficina: 9 } }) };
      };
      await docs.sincronizar();
      const enviados = peticiones.filter((p) => p.ruta === "/s/").map((p) => [p.cuerpo.revision_base, p.cuerpo.datos]);
      console.log(JSON.stringify({ enviados, local: almacen.get("L") }));
    """)
    assert r["enviados"] == [[1, {"a": 1}], [1, {"a": 2}]]                  # la segunda sigue con la base 1: el servidor fusiona otra vez
    assert r["local"]["revision_base"] == 4 and r["local"]["datos"] == {"a": 2, "oficina": 9} and r["local"]["sucio"] is False


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


def test_lo_que_no_viene_en_la_lista_se_pregunta_y_nunca_se_olvida_su_servidor_id():
    r = correr(r"""
      const base = { tipo: "cotizacion", cuenta: 1, datos: { a: 1 }, actualizada: 100 };
      almacen.set("A", Object.assign({ id: "A", servidor_id: "SA", revision_base: 1, sucio: false }, base));   // viene en la página 2
      almacen.set("B", Object.assign({ id: "B", servidor_id: "SB", revision_base: 1, sucio: false }, base));   // en la papelera, sin cambios
      almacen.set("C", Object.assign({ id: "C", servidor_id: "SC", revision_base: 1, sucio: true }, base));    // en la papelera, con cambios
      almacen.set("D", Object.assign({ id: "D", servidor_id: "SD", revision_base: 1, sucio: false }, base));   // no vino en la lista pero existe
      servidor = async (ruta) => {
        if (ruta === "/s/") return { status: 503, body: { error: "ocupado" } };            // C no se puede subir ahora
        if (ruta === "/l/") return { status: 200, body: { cotizaciones: [], siguiente: 2 } };
        if (ruta === "/l/?pagina=2") return { status: 200, body: { cotizaciones: [fila("SA", 1)], siguiente: null } };
        if (ruta === "/d/SB/" || ruta === "/d/SC/") return { status: 410, body: { error: "Está en la papelera.", eliminado: true } };
        if (ruta === "/d/SD/") return { status: 200, body: Object.assign(fila("SD", 2), { datos: { a: 5 } }) };
        return { status: 404, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify({ ids: [...almacen.keys()].sort(), C: almacen.get("C"), D: almacen.get("D"), rutas: peticiones.map((p) => p.ruta) }));
    """)
    assert r["ids"] == ["A", "C", "D"]                                           # B (papelera, sin cambios) se quitó; A y D siguen
    assert r["C"]["servidor_id"] == "SC" and r["C"]["sucio"] is True and r["C"]["eliminado_en_servidor"] is True
    assert r["D"]["revision_base"] == 2 and r["D"]["datos"] == {"a": 5}
    assert "/l/?pagina=2" in r["rutas"]


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
