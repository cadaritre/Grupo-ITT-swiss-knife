/* Orden de las fotos de un reporte: reparto por página del PDF y arrastre con Pointer Events (ratón, dedo y lápiz).
 *
 *   ITT.ordenFotos.paginas(n, porPagina, conCroquis)  -> [[índices de la portada], [página 2], …]
 *       La misma regla que `dominio.pdf_generator.photo_pages` (una prueba compara las dos).
 *   ITT.ordenFotos.arrastre(contenedor, opciones)      -> { cancelar() }
 *       opciones.elementos  selector de los elementos que se ordenan (llevan data-indice)
 *       opciones.asa        selector del asa: con ella el arrastre empieza al instante (touch-action: none en el CSS)
 *       opciones.mantener   selector de lo que se arrastra con el dedo tras mantenerlo presionado (la página sigue desplazándose)
 *       opciones.rejilla    true: el destino se calcula en dos dimensiones (miniaturas); si no, solo en vertical (lista)
 *       opciones.alSoltar(desde, hasta)   índices finales; no se llama si la foto queda donde estaba
 *       opciones.alTocar(indice)          toque sin arrastre sobre un elemento de `mantener`
 * Sin dependencias. Escape cancela. La lista se desplaza sola cerca de los bordes, más rápido cuanto más cerca.
 */
(function () {
  "use strict";
  const ITT = (self.ITT = self.ITT || {});

  function paginas(n, porPagina, conCroquis) {
    if (![1, 2, 4, 6].includes(porPagina)) throw new Error("fotos por página: 1, 2, 4 o 6");
    const primera = conCroquis ? 0 : Math.min(1, n);
    const salida = [Array.from({ length: primera }, (_, i) => i)];
    for (let i = primera; i < n; i += porPagina) salida.push(Array.from({ length: Math.min(porPagina, n - i) }, (_, k) => i + k));
    return salida;
  }

  const UMBRAL = 6;                 // px que mueve el ratón antes de que un toque sea arrastre
  const MANTENER_MS = 350;          // dedo: tiempo presionado antes de arrastrar (antes de eso, desplaza la página)
  const BORDE = 80;                 // px desde el borde de la ventana donde empieza el desplazamiento automático
  const VELOCIDAD = 22;             // px por cuadro, al tocar el borde

  function arrastre(contenedor, op) {
    let estado = null;              // { el, desde, id, x, y, dx, dy, activo, fantasma, marca, destino, temporizador, cuadro, origen }
    let tocado = false;             // el último toque fue un arrastre: el clic que sigue no abre nada

    const elementos = () => [...contenedor.querySelectorAll(op.elementos)];

    function iniciar(e, el, inmediato) {
      estado = { el, desde: Number(el.dataset.indice), id: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY,
        activo: false, tipo: e.pointerType, destino: null, temporizador: null, cuadro: 0, origen: e.target };
      try { e.target.setPointerCapture(e.pointerId); } catch (_) { /* evento sintético */ }
      addEventListener("pointermove", mover, true);
      addEventListener("pointerup", soltar, true);
      addEventListener("pointercancel", cancelar, true);
      addEventListener("keydown", tecla, true);
      document.addEventListener("touchmove", bloquearDesplazamiento, { passive: false, capture: true });
      if (inmediato) activar();
      else if (e.pointerType === "touch") estado.temporizador = setTimeout(activar, MANTENER_MS);
    }

    function activar() {
      if (!estado || estado.activo) return;
      const r = estado.el.getBoundingClientRect();
      estado.activo = true;
      estado.dx = estado.x0 - r.left; estado.dy = estado.y0 - r.top;
      const f = estado.el.cloneNode(true);
      f.removeAttribute("id");
      for (const n of f.querySelectorAll("[id]")) n.removeAttribute("id");
      f.classList.add("rep-fantasma");
      f.setAttribute("aria-hidden", "true");
      f.style.width = `${r.width}px`; f.style.height = `${r.height}px`;
      document.body.append(f);
      estado.fantasma = f;
      estado.marca = Object.assign(document.createElement("div"), { className: `rep-destino${op.rejilla ? " rep-destino--vertical" : ""}` });
      estado.marca.setAttribute("aria-hidden", "true");
      document.body.append(estado.marca);
      estado.el.classList.add("rep-arrastrado");
      document.documentElement.classList.add("rep-arrastrando");
      if (estado.tipo === "touch" && navigator.vibrate) try { navigator.vibrate(10); } catch (_) { /* sin vibración */ }
      colocar();
      estado.cuadro = requestAnimationFrame(desplazar);
    }

    function mover(e) {
      if (!estado || e.pointerId !== estado.id) return;
      estado.x = e.clientX; estado.y = e.clientY;
      if (!estado.activo) {
        const lejos = Math.hypot(estado.x - estado.x0, estado.y - estado.y0) > UMBRAL;
        if (!lejos) return;
        if (estado.tipo === "touch") { terminar(); return; }                 // el dedo se movió antes de tiempo: es desplazar la página
        activar();
      }
      e.preventDefault();
      colocar();
    }

    function bloquearDesplazamiento(e) { if (estado && estado.activo) e.preventDefault(); }

    // Índice de inserción (0..n) según el puntero, sobre los elementos en su lugar original.
    function calcularDestino() {
      const lista = elementos();
      for (let i = 0; i < lista.length; i++) {
        const r = lista[i].getBoundingClientRect();
        if (op.rejilla) {
          if (estado.y < r.top || (estado.y <= r.bottom && estado.x < r.left + r.width / 2)) return { indice: i, r, antes: true };
        } else if (estado.y < r.top + r.height / 2) return { indice: i, r, antes: true };
      }
      const ultimo = lista[lista.length - 1];
      return { indice: lista.length, r: ultimo.getBoundingClientRect(), antes: false };
    }

    function colocar() {
      const { fantasma, marca } = estado;
      fantasma.style.transform = `translate(${estado.x - estado.dx}px, ${estado.y - estado.dy}px)`;
      const d = calcularDestino();
      estado.destino = d.indice;
      const sinCambio = d.indice === estado.desde || d.indice === estado.desde + 1;
      marca.hidden = sinCambio;
      if (op.rejilla) {
        const x = d.antes ? d.r.left - 4 : d.r.right + 2;
        marca.style.transform = `translate(${x}px, ${d.r.top}px)`;
        marca.style.height = `${d.r.height}px`;
      } else {
        const y = d.antes ? d.r.top - 8 : d.r.bottom + 4;
        marca.style.transform = `translate(${d.r.left}px, ${y}px)`;
        marca.style.width = `${d.r.width}px`;
      }
    }

    function desplazar() {
      if (!estado || !estado.activo) return;
      const alto = innerHeight;
      let paso = 0;
      if (estado.y < BORDE) paso = -VELOCIDAD * Math.pow((BORDE - Math.max(estado.y, 0)) / BORDE, 2);
      else if (estado.y > alto - BORDE) paso = VELOCIDAD * Math.pow((Math.min(estado.y, alto) - (alto - BORDE)) / BORDE, 2);
      if (paso) {
        const antes = scrollY;
        scrollBy(0, Math.round(paso) || Math.sign(paso));
        if (scrollY !== antes) colocar();
      }
      estado.cuadro = requestAnimationFrame(desplazar);
    }

    function soltar(e) {
      if (!estado || e.pointerId !== estado.id) return;
      const { activo, destino, desde } = estado;
      terminar();
      if (!activo) return;                                                     // fue un toque: el clic que sigue lo atiende
      tocado = true;
      setTimeout(() => { tocado = false; }, 0);
      e.preventDefault();
      if (destino == null) return;
      const hasta = destino > desde ? destino - 1 : destino;
      if (hasta !== desde) op.alSoltar(desde, hasta);
    }

    function tecla(e) { if (e.key === "Escape" && estado) { e.preventDefault(); e.stopPropagation(); terminar(); } }
    function cancelar(e) { if (estado && (!e || e.pointerId === estado.id)) terminar(); }

    function terminar() {
      if (!estado) return;
      clearTimeout(estado.temporizador);
      cancelAnimationFrame(estado.cuadro);
      if (estado.fantasma) estado.fantasma.remove();
      if (estado.marca) estado.marca.remove();
      estado.el.classList.remove("rep-arrastrado");
      document.documentElement.classList.remove("rep-arrastrando");
      removeEventListener("pointermove", mover, true);
      removeEventListener("pointerup", soltar, true);
      removeEventListener("pointercancel", cancelar, true);
      removeEventListener("keydown", tecla, true);
      document.removeEventListener("touchmove", bloquearDesplazamiento, { capture: true });
      estado = null;
    }

    contenedor.addEventListener("pointerdown", (e) => {
      if (estado || (e.pointerType === "mouse" && e.button !== 0)) return;
      const asa = op.asa && e.target.closest(op.asa);
      const mantener = !asa && op.mantener && e.target.closest(op.mantener);
      const el = (asa || mantener) && (asa || mantener).closest(op.elementos);
      if (!el || !contenedor.contains(el)) return;
      if (asa) e.preventDefault();
      iniciar(e, el, !!asa);
    });
    // Un clic justo después de soltar no abre la hoja; sin arrastre, el clic es un toque.
    contenedor.addEventListener("click", (e) => {
      if (tocado) { e.preventDefault(); e.stopPropagation(); tocado = false; return; }
      const m = op.mantener && e.target.closest(op.mantener);
      if (m && op.alTocar) { const el = m.closest(op.elementos); if (el) op.alTocar(Number(el.dataset.indice)); }
    }, true);
    contenedor.addEventListener("contextmenu", (e) => { if (estado) e.preventDefault(); });

    return { cancelar: () => terminar() };
  }

  ITT.ordenFotos = { paginas, arrastre };
})();
