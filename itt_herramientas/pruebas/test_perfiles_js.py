"""El teléfono separa los perfiles corporativos (`offline/documentos.js`, en Node con la cola y el servidor de mentira de
`test_documentos_js`): la lista solo muestra el perfil activo, cada perfil tiene su cursor, lo del otro perfil nunca se borra al sincronizar
el activo, lo pendiente sube con su perfil sea cual sea el activo, y si el perfil activo cambió en otro dispositivo se baja el del nuevo."""
from __future__ import annotations

import shutil

import pytest

from .test_documentos_js import correr

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")

REGISTROS = r"""
  const reg = (id, perfil, extra) => Object.assign({ id, tipo: "cotizacion", cuenta: 1, perfil, servidor_id: "S" + id, revision_base: 1, parcial: true,
    resumen: { id: "S" + id, perfil }, datos: {}, sucio: false, borrada: false, actualizada: 1 }, extra || {});
  ajustes.perfil = "grupo_itt";
  almacen.set("A", reg("A", "grupo_itt"));
  almacen.set("T", reg("T", "tresvizo"));
  almacen.set("V", reg("V", undefined, { resumen: { id: "SV", perfil: "tresvizo" } }));       // guardado antes de separar: su resumen lo dice
"""


def test_la_lista_es_del_perfil_activo_y_cada_perfil_tiene_su_cursor():
    r = correr(REGISTROS + r"""
      const nuevo = await docs.crear({ quote_number: "" });
      const antes = (await docs.listar()).map((b) => b.id).sort();
      servidor = async (ruta, o) => {
        if (ruta === "/s/") return { status: 200, body: { resultado: "creada", servidor_id: "SN", revision: 1, folio: "COT-2026-0005", perfil: "grupo_itt" } };
        // Grupo ITT: se borró algo para siempre y solo sigue SA. Lo de TresVizo no está en esa lista y no se debe tocar.
        if (ruta === "/l/?desde=0") return { status: 200, body: { cotizaciones: [], eliminados: [], vigentes: ["SA", "SN"], cursor: 7, mas: false, perfil: "grupo_itt" } };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();
      const subida = peticiones.find((p) => p.ruta === "/s/").cuerpo;
      const despues = [...almacen.keys()].sort();
      // Ahora el perfil activo cambió en otro dispositivo: la lista de Grupo ITT lo dice y se baja TresVizo con SU cursor (desde 0).
      peticiones.length = 0;
      servidor = async (ruta) => {
        if (ruta === "/l/?desde=7") return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 7, mas: false, perfil: "tresvizo" } };
        if (ruta === "/l/?desde=0") return { status: 200, body: { cotizaciones: [{ id: "ST", revision: 1, actualizada: "2026-11-17T10:00:00Z", perfil: "tresvizo" }],
          eliminados: [], cursor: 12, mas: false, perfil: "tresvizo" } };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify({ antes, nuevo: nuevo.id, perfilNuevo: almacen.get(nuevo.id).perfil, subida: subida.perfil, despues,
        rutas: peticiones.map((p) => p.ruta), cursores: [ajustes["cursor:cotizacion:1:grupo_itt"], ajustes["cursor:cotizacion:1:tresvizo"]],
        activo: ajustes.perfil, eventos: eventos.filter((e) => e && e.perfil), ahora: (await docs.listar()).map((b) => b.id).sort() }));
    """)
    assert r["antes"] == sorted(["A", r["nuevo"]]) and r["perfilNuevo"] == "grupo_itt"      # lo nuevo es del perfil activo
    assert r["subida"] == "grupo_itt"
    assert r["despues"] == sorted(["A", "T", "V", r["nuevo"]])                              # los vigentes de Grupo ITT no borran TresVizo
    assert r["rutas"] == ["/l/?desde=7", "/l/?desde=0"] and r["cursores"] == [7, 12] and r["activo"] == "tresvizo"
    assert r["eventos"] == [{"perfil": "tresvizo", "antes": "grupo_itt"}]
    assert r["ahora"] == ["T", "V"]


def test_lo_pendiente_de_otro_perfil_sube_con_su_perfil_y_lo_ajeno_se_suelta():
    r = correr(REGISTROS + r"""
      almacen.set("P", reg("P", "tresvizo", { servidor_id: null, revision_base: null, parcial: false, sucio: true, datos: { a: 1 }, actualizada: 5 }));
      almacen.set("Q", reg("Q", "tresvizo", { parcial: false, sucio: true, datos: { a: 2 }, actualizada: 6 }));
      servidor = async (ruta, o) => {
        if (ruta === "/s/") {
          const c = JSON.parse(o.body);
          if (c.id_cliente === "P") return { status: 200, body: { resultado: "creada", servidor_id: "SP", revision: 1, perfil: "tresvizo", ajeno: false } };
          return { status: 200, body: { resultado: "actualizada", servidor_id: "SQ", revision: 2, perfil: "tresvizo", ajeno: true } };
        }
        if (ruta.startsWith("/l/")) return { status: 200, body: { cotizaciones: [], eliminados: [], cursor: 3, mas: false, perfil: "grupo_itt" } };
        return { status: 500, body: {} };
      };
      await docs.sincronizar();
      console.log(JSON.stringify({ subidas: peticiones.filter((p) => p.ruta === "/s/").map((p) => [p.cuerpo.id_cliente, p.cuerpo.perfil]).sort(),
        P: almacen.get("P"), Q: almacen.get("Q") || null, lista: (await docs.listar()).map((b) => b.id), ajenos: eventos.filter((e) => e && e.tipo_evento === "ajeno").map((e) => e.id) }));
    """)
    assert r["subidas"] == [["P", "tresvizo"], ["Q", "tresvizo"]]                          # con Grupo ITT activo, cada uno con el suyo
    assert r["P"]["sucio"] is False and r["P"]["servidor_id"] == "SP" and r["P"]["perfil"] == "tresvizo"
    assert r["Q"] is None and r["ajenos"] == ["Q"]                                         # quedó en el servidor; aquí ya no se muestra
    assert r["lista"] == ["A"]                                                              # P sigue guardado, en el espacio de TresVizo
