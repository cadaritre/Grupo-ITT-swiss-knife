# CAD en Herramientas

CAD es la biblioteca compartida de planos DWG y DXF de Herramientas (`/cad/`, grupo «Gabinete»), con el editor Open CAD Studio dentro de
la página. Código: `itt_herramientas/cad/`; plantillas `templates/herramientas/cad/`; estáticos `static/herramientas/cad/`; pruebas
`pruebas/test_cad.py` y `pruebas_navegador/test_cad_navegador.py`. Despliegue y verificación en el servidor: `despliegue/SALIDA-DE-CAD.md` del
Portal.

## El editor fijado

`itt_herramientas/cad/editor/upstream.json` fija la versión, la **fuente** y el **SHA-256** de cada pieza:

- `web`: la compilación web (ZIP de Trunk, ~63 MB, wasm de 98 MB / 33 MB con gzip);
- `nativo.x86_64` y `nativo.aarch64`: el AppImage de Linux que imprime y convierte sin pantalla (el x86_64 para el servidor; el aarch64 para
  la VM de ensayo).
- `fuente`: `{"tipo": "publica", "repo": "HakanSeven12/OpenCADStudio", "tag": "v2026.45"}` (descarga directa) o
  `{"tipo": "gh", "repo": "cadaritre/OpenCADStudio-TresVizo", "tag": …}` para nuestra versión corregida en el repositorio privado: se baja con
  `gh release download` y la sesión de gh de la Mac. Una pieza puede traer su propia `fuente`. El servidor nunca descarga nada.
- `puente`: si la compilación trae el puente `?embed=1` (llegó a `main` de Open CAD Studio el 14-11-2026, después de v2026.45). Sin él, la
  página usa el canal de control y `openCadStudioSaveFile`, y «solo lectura» lo hace cumplir la página (no se guarda), no el editor.

`cad/editor/preparar.py` (solo biblioteca estándar) baja una pieza, comprueba tamaño y SHA-256 y, para el editor, lo extrae y pone encima
nuestra página de arranque (`index.html`, `arranque.js`, `editor.css`): sin scripts en línea, sin la analítica de la página oficial, sin service
worker, y con las listas de videos, conversaciones y patrocinadores vacías. Deja `MANIFIESTO.json` (SHA-256 de cada archivo) y un `.gz` de
cada archivo grande para Caddy.

```sh
python3 itt_herramientas/cad/editor/preparar.py editor --destino ~/.cache/tresvizo-cad/editor
python3 itt_herramientas/cad/editor/preparar.py nativo --arch aarch64 --destino DIR
python3 itt_herramientas/cad/editor/preparar.py verificar --destino ~/.cache/tresvizo-cad/editor
```

**Actualizar:** cambiar versión, fuente, nombres, tamaños y SHA-256 (y `puente`) en `upstream.json`; correr las pruebas, también las del navegador.

### Red a terceros

Lo que se encontró en v2026.45 y cómo queda:

| Llamada | Qué hace | Cómo se corta |
| --- | --- | --- |
| `https://open-aec.com/shared/analytics.js` | analítica en la página oficial | nuestra página no la carga |
| `videos.json` → miniaturas `https://i.ytimg.com/…` | pantalla de inicio | `videos.json` vacío y CSP `img-src 'self'` |
| `discussions.json`, `supporters.json` | conversaciones y patrocinadores de GitHub | listas vacías |
| `sw.js` | service worker propio del editor | no se sirve ni se registra |
| enlaces a GitHub, Patreon, Reddit, YouTube, descarga de escritorio | solo si la persona los pulsa (`window.open`) | no son llamadas en segundo plano |
| revisión de actualizaciones | no existe en la compilación web | — |

La CSP de `/cad/editor/` (`cad/editor/__init__.py`, igual que en `tools.caddy` del Portal) deja `connect-src`, `img-src` y `font-src` en
`'self'`: cualquier otra llamada a terceros se bloquea. Las fuentes Noto vienen dentro del paquete.

## La página del editor

- Abre la versión vigente (o una anterior con `?version=n`, o el borrador) por el canal de control (`op: open`) o por `ocs:open` (con puente).
- **Guardar** (botón y Ctrl/⌘+S de la página) pide los bytes por el canal (`op: save`); lo que la persona guarda dentro del editor llega por
  `openCadStudioSaveFile` o `ocs:save`. Todo termina en `POST /cad/<id>/guardar/` con el bloqueo de esta página y la versión de base: si el
  plano cambió o se perdió el bloqueo, responde 409 y se ofrece «Guardar como copia». Nada se pisa.
- «Sin guardar» sale de la revisión del dibujo en el editor (`op: state`, cada segundo) contra la del último guardado.
- Bloqueo con latido (60 s; caduca a los 150: un navegador deja latir una pestaña en segundo plano una vez por minuto). Borrador de
  recuperación solo si algo cambió desde el anterior: a lo más uno cada 2 minutos mientras hay cambios y al pasar la app a segundo plano;
  mirar el plano nunca manda borradores. Sin el aviso «¿Abandonar el sitio?».
- **Lo que se sube** (guardar, copia, borrador) va con gzip si así pesa al menos un 10 % menos (`Content-Encoding: gzip`, `cad/sesion.js`):
  el servidor lo descomprime de a 1 MB con el límite sobre lo descomprimido y guarda el plano tal cual.
- En el teléfono (pantalla ≤ 767 px y puntero táctil) los planos se abren para ver, salvo con el «Modo táctil» encendido (abajo).
- **Si no se puede guardar** (biblioteca llena, plano demasiado grande, servidor caído, sin red) el aviso ofrece «Descargar mis cambios»
  (también en la hoja de conflicto): los bytes del editor bajan a este equipo con el nombre del plano. Nada se da por guardado.
- **Si la sesión termina** (cerrarla en otra pestaña, la revoca quien administra) la página no se recarga: una franja fija dice que hay que
  entrar de nuevo en otra pestaña, el latido se pausa y se ofrece descargar los cambios; al volver a la pestaña con sesión, todo sigue.
- **Borrador pendiente**: mientras el aviso «Tienes cambios sin guardar de la última vez» no se resuelve, no se guardan borradores nuevos
  (pisarían ese); al primer cambio una hoja pide elegir: recuperarlos, descartarlos o guardarlos como copia.
- **Bloqueo retomado tras dormir**: si el latido vuelve a tomar el bloqueo y el plano tiene una versión nueva de otra persona, se suelta el
  bloqueo, se dice quién guardó y lo de aquí se guarda como copia (o «Editar ahora» abre la versión nueva).
- **Copias en el navegador**: el editor guarda una copia de cada plano que abre (sus «recientes»), su autoguardado y los nombres de su
  sesión en el almacenamiento del navegador. Herramientas los borra al arrancar el editor (antes de cargarlo), al salir de la página del
  plano, al cargar cualquier otra página de Herramientas y al cerrar sesión (`cad/editor/arranque.js`, `static/herramientas/cad/sesion.js`).

## Extensiones de la página: `window.TVCad`

`static/herramientas/cad/control.js` (versión 1) da a otras partes de la página (la «Civil») el canal de control del editor y la barra
`#cad-extensiones`, encima del editor. Su JS se agrega en `{% block extensiones_cad %}` de `cad/plano.html`.

| API | Qué hace |
| --- | --- |
| `TVCad.listo` | promesa que se cumple con el estado cuando el editor abrió el plano y su canal respondió |
| `TVCad.estaListo()` | `true` / `false` |
| `TVCad.estado()` | `{listo, editable, plano: {id, nombre, formato, revision}}` (`editable` = esta página tiene el bloqueo) |
| `await TVCad.pedir(peticion, {tiempoMs})` | manda una petición del canal (API-SPEC de Open CAD Studio) y completa `protocol`, `request_id` y `document_id` |
| `await TVCad.ejecutar("LINE 0,0 10,10")` | atajo de `{op: "run", cmd}` |
| `TVCad.barra()` | el elemento `#cad-extensiones` (`role="toolbar"`), ya visible |
| `TVCad.ocupar()` / `soltar()` / `ocupado()` | una extensión avisa que trabaja con el editor (espera una elección en el dibujo, inserta un resultado): mientras tanto no se guarda el borrador de recuperación y «Guardar» pide esperar, porque el editor rechaza guardar a media operación |
| `TVCad.vistaDe(state)` / `await TVCad.vista()` | pantalla ↔ mundo del editor: `{cx, cy, k, w, h, ox, oy, aMarco, deMarco, aPagina, dePagina}` desde `state.camera` y el área de dibujo (`state.viewport_rect` y `screen_scale` desde tv-v2026.45-8; sin ellos, el lienzo del editor, exacto con el lienzo solo). **Compartida**: la usan el modo táctil (`deEstado`) y Civil (la vista previa al colocar), así nunca difieren |
| `TVCad.on(evento, fn)` / `off` | `listo`, `cargado`, `cambio` (`{sucio}`), `guardado` (`{revision, formato}`) |

`pedir` rechaza con `TVCad.ErrorEditor` y su `codigo`: `no_listo` (aún no abre el plano), `sin_canal`, `tiempo` o `rechazada` (respuesta
`ok: false`, completa en `error.respuesta`). Guardar sigue siendo de la página: un cambio por el canal marca el plano «Sin guardar».

El canal (`?automation=1`) solo se alcanza desde el mismo origen: el editor vive en un marco de `tools.tresvizo.com`, su CSP pone
`frame-ancestors 'self'` y el navegador no deja que otro origen toque el marco.

## El tamaño del editor y «Pantalla completa»

- El editor llega siempre hasta abajo de la ventana: su alto es lo que queda debajo de la cabecera, los avisos y la barra de Civil,
  medido de verdad (`static/herramientas/cad/pantalla.js`); si lo de arriba no deja ni 360 px (teléfono), mide 360 y la página se desplaza.
- **«Pantalla completa»** (botón en la cabecera del plano, Ctrl/⌘+Mayús+F, F11; también en «Más» del modo táctil): fuera el lateral,
  la cabecera de Herramientas y los datos del plano. Quedan una tira con el nombre, el estado, Guardar, Versiones y «Salir de pantalla
  completa», la barra de Civil y el editor en todo lo demás. Usa la pantalla completa del navegador donde existe; donde no (Safari de
  iPhone), la página se maximiza dentro de la ventana. **Esc nunca sale** (es del editor: cancelar y deseleccionar; pedido del dueño,
  16-11-2026): en Chromium Esc se bloquea para el navegador (`navigator.keyboard.lock(["Escape"])`, el navegador avisa «mantén presionada
  Esc para salir»); en Safari y Firefox, si Esc quita la pantalla del navegador, la página sigue maximizada y lo dice una vez
  (`localStorage tv.cad.completa-aviso-esc`). Solo el botón, Ctrl/⌘+Mayús+F o F11 la quitan. Se recuerda en el dispositivo (`localStorage tv.cad.completa`): al
  volver se abre maximizado en la página, porque el navegador solo deja entrar a su pantalla completa con un toque o una tecla.

## Esc (18-11-2026)

Pedido del dueño (Chrome en Windows y en la Mac, «Modo completo»): «el Esc sigue siendo un problema». **Un Esc hace lo que en AutoCAD**,
esté donde esté el foco en la página del plano, y **nunca** sale de la pantalla completa ni de la página maximizada, ni cierra, ni navega,
ni pierde trabajo.

**Las causas** (todas arregladas):

1. **El foco en nuestra página.** El editor solo oye el teclado con el foco en su lienzo. Después de un clic en la cabecera, en Guardar, en
   «Pantalla completa» (el foco quedaba en el botón), en la barra de Civil o al cerrar una hoja nuestra, Esc llegaba a nuestra página y nadie
   lo atendía: la orden seguía y la selección también. Ahora la página lo reenvía al editor (`editor.js`, «Esc es del editor») y devuelve el
   foco al dibujo.
2. **El canal rechazaba el Esc a media orden.** Con una orden de la persona en curso, el editor rechaza las peticiones de la página
   (`command_busy`) salvo `cancel`: la página usa `cancel` con orden y `command_escape` sin ella.
3. **Dos Esc con texto escrito.** Con coordenadas escritas en la línea de órdenes (LINE, «10,1»), el primer Esc solo vaciaba la línea y hacía
   falta otro para la orden; PLINE pedía dos desde sus subopciones (Grosor, Longitud…). En el editor (tv-v2026.45-12, parche 53): un Esc
   termina la orden, como AutoCAD.
4. **Atajos que el editor se quedaba.** Con el foco en el editor, F11 era la pantalla completa del navegador (sin la página maximizada ni
   Esc bloqueado) y Ctrl/⌘+Mayús+S el «Guardar como» del editor (una descarga). `cad/editor/arranque.js` los manda a la página, con
   Ctrl/⌘+S y Ctrl/⌘+Mayús+F.

**Lo que hace Esc, camino por camino** (pruebas: `test_cad_pestanas_navegador.py`, `test_un_esc_cancela_y_deselecciona_en_cada_camino`,
Chromium con el agente de Windows y WebKit):

| Dónde está el foco | Qué pasa |
| --- | --- |
| Lienzo del editor, con una orden (con o sin texto escrito) | un Esc la cancela |
| Lienzo, sin orden, con selección o pinzamientos | un Esc los quita |
| Un diálogo o paleta del editor | lo cierra el editor (uno por Esc, como en AutoCAD) |
| Nuestra cabecera, un botón, la barra de Civil | se reenvía al editor: cancela la orden o deselecciona, y el foco vuelve al dibujo |
| Una hoja nuestra abierta (Versiones, Guardar todo, Abrir otro plano…) | la cierra; el siguiente Esc es del editor |
| El menú «Más acciones» abierto | lo cierra |
| Un campo de texto de la página (renombrar, buscar, Civil) | es del campo |
| «Pantalla completa» (con el botón, el atajo o F11) | igual que arriba; en Chromium Esc está bloqueado para el navegador, que solo sale con Esc sostenida |
| La hoja «¿Qué hacemos con tus cambios de la última vez?» | no se cierra con Esc: pide una respuesta |

En Safari y Firefox el navegador quita su propia pantalla completa con Esc (no hay forma de impedirlo): la página sigue maximizada, lo dice
una vez, y el siguiente Esc es del editor.

## Varios planos en una ventana (18-11-2026)

Pedido del dueño: «una manera elegante de manejar múltiples archivos abiertos en la misma ventana». En la computadora (no en el modo
táctil, que sigue con un plano por ventana, ni en una versión anterior), **cada plano abierto es una pestaña** encima del editor, como las
pestañas de archivo de AutoCAD; también en pantalla completa.

- **Abrir.** El botón **+** de la tira y «Abrir otro plano» en «Más acciones» abren una hoja con la biblioteca: búsqueda (nombre, proyecto o
  persona), los proyectos o clientes como carpetas, quién edita cada plano y cuáles ya están abiertos (uno abierto no se abre dos veces: se
  va a su pestaña). Duplicar abre la copia en otra pestaña.
- **Cada pestaña es un plano completo del servidor**: su bloqueo (tomado y con latido por pestaña: cada una es una página del editor para el
  servidor), sus versiones, su borrador de recuperación, «Sin guardar», solo lectura y permisos, la recuperación al abrir y el ETag/304.
  Cambiar de pestaña cambia la cabecera: nombre, formato, estado, Guardar, Versiones, el menú y los avisos.
- **La tira** muestra el nombre de cada plano, un punto si tiene cambios sin guardar y un candado si está en solo lectura. La × (o el botón
  central del ratón) cierra esa pestaña: si tiene cambios, pregunta solo por ella («¿Guardar los cambios de «Plano»?»: Cancelar, Cerrar sin
  guardar, Guardar y cerrar). Cerrar la última vuelve a «Planos».
- **Guardar** (Ctrl/⌘+S) guarda la pestaña activa. **Guardar todo** (Ctrl/⌘+Mayús+S y en «Más acciones», con cuántos) guarda cada pestaña
  con cambios como su propia versión. Si todas se guardan, un aviso («Se guardaron 3 planos.»); si alguna no, la hoja «Se guardaron 1 de 2
  planos» dice cuáles sí (con su versión) y cuáles no y por qué («No se guardó: Beto Ruiz tomó el control de este plano…»), con «Ver» para
  ir a esa pestaña. Nada se guarda como copia sin pedirlo.
- **Salir** con cambios en varias pestañas pregunta una vez y las lista.
- **Copiar y pegar entre pestañas** funciona: todas viven en el mismo editor (COPYCLIP en una, PASTECLIP en otra).
- **Recargar** vuelve a abrir las mismas pestañas, en su orden y con la misma activa, cada una con su recuperación de borrador de siempre.
  Se recuerdan en este dispositivo, por ventana del navegador (`localStorage tv.cad.pestanas`, la ventana en `sessionStorage`): una ventana
  no pisa las pestañas de otra.
- **Un dibujo que no es de la biblioteca** (NUEVO dentro del editor, o un archivo de este equipo con ABRIR) sale como pestaña «Fuera de la
  biblioteca»: «Guardar en la biblioteca» lo deja como plano nuevo (`POST /cad/desde/editor/`) y la pestaña pasa a ser ese plano.
- **Guardar como copia** (conflicto, bloqueo perdido) deja la copia en la misma pestaña, sin salir de la página.
- **Máximo**: 8 pestañas (`HERRAMIENTAS_CAD_PESTANAS_MAX`) y, entre todas, 64 MB de planos (`HERRAMIENTAS_CAD_PESTANAS_BYTES`). El editor
  web vive en una memoria de 4 GB como máximo y no la devuelve al cerrar una pestaña. Medido el 18-11-2026 (Chromium, la compilación de
  esta rama, `test_memoria_con_varios_planos` con los planos de `tresvizo/pruebas/generar.py --grande` del fork):

  | Pestañas | El que se abrió | Memoria del editor |
  | --- | --- | --- |
  | 1 | levantamiento UTM de 3.9 MB | 226 MB |
  | 2 | 3.9 MB | 335 MB |
  | 3 | 3.9 MB | 456 MB |
  | 4 | 50 000 puntos, 58.6 MB | 2 642 MB |
  | 5–6 | 3.9 MB | 2 642 MB (reusa lo que soltó el grande) |
  | 8 | 3.9 MB | 2 818 MB |

  Un plano normal toma ~0.11 GB; el más grande que acepta la biblioteca (50 MB), más de 2 GB. Con 8 y 64 MB cabe uno grande con siete
  normales, y nunca dos grandes. Al llegar: «Ya tienes 8 planos abiertos, el máximo en una ventana. Cierra uno para abrir otro.» o «Los
  planos abiertos ya ocupan casi toda la memoria del editor en el navegador. Cierra uno para abrir «Plano».»
  **Las imágenes de la carpeta del proyecto no cuentan en ese tope, a propósito:** no son parte del plano y su memoria tiene su propio
  límite, que no crece con las pestañas. El editor tiene los píxeles de la pestaña a la vista (lo que su pantalla necesita, ver «Ortofotos
  enormes») y, de las de atrás, solo sus vistas reducidas, hasta 256 MB entre todas (sin detalles; sale primero la de la pestaña vista
  hace más tiempo). Ver «Con varias pestañas» en «Carpeta del proyecto».

**Servidor:** `GET /cad/<id>/pestana/` (lo de un plano para abrirlo en una pestaña), `GET /cad/elegir/` (la hoja), `POST /cad/desde/editor/`,
y Duplicar, Eliminar y Renombrar responden JSON con `Accept: application/json`. **Editor:** el canal ya abría, activaba, cerraba y guardaba un
documento dado; tv-v2026.45-12 agrega `documents`, `file_tabs` y el documento en `openCadStudioSaveFile` (contrato en
`docs/tresvizo-documentos.md` del fork). `window.TVCadPestanas` (lista, abrir, activar, guardarTodo) es para las pruebas.

## Modo táctil (teléfono y tableta) — detrás de `TV_CAD_TACTIL`

Apagado por omisión (`settings.TV_CAD_TACTIL` o la variable de entorno del mismo nombre: «1»). Apagado, nada cambia: la página del plano es
la de siempre, el service worker es el mismo byte a byte y `/cad/sin-conexion/` y `/cad/tactil/paquete/` responden 404 (`cad/tactil.py`).

- **Qué es.** Una capa táctil en nuestra página (`static/herramientas/cad/tactil.{js,css}`, `templates/herramientas/cad/tactil.html`) encima
  del editor, que lo maneja por su canal de control (`window.TVCad`). El editor dibuja; la página (`editor.js`) sigue siendo la dueña de
  guardar, versiones, bloqueo y borradores. Diseño (a) de la compuerta del 15-11-2026.
- **Cuándo.** Interruptor «Modo táctil» en la cabecera del plano, recordado por dispositivo (`localStorage tv.cad.tactil`); la primera vez en
  un dispositivo solo táctil abre ya en modo táctil. «Más › Modo completo» vuelve al editor de siempre sin recargar el plano.
- **Gestos.** Un dedo mueve; dos dedos acercan alrededor de los dedos; mantener pulsado = el puntero. Los toques son una sola máquina
  («racha»: toques seguidos a ≤ 320 ms y ≤ 40 px del primero): un toque elige al momento; toque y, otra vez en el mismo lugar, arrastre =
  recuadro de selección (izquierda → derecha, azul, lo que queda dentro; al revés, verde punteado, lo que toca) que suma a la selección
  y corre el plano solo cuando el dedo llega al borde; tres toques = ver todo. Lo que cambiaron los primeros toques (selección, punta,
  un recorte) se deshace antes del recuadro o de ver todo. Un segundo toque tarde o lejos es un toque o un arrastre nuevos.
  «Más › Seleccionar área» sigue como alternativa. Durante el gesto el marco del editor se mueve en el compositor del navegador y al
  soltar `view_set` (f64) lo deja donde se ve.
- **Puntero con desfase.** La punta queda 88 px arriba del asa (96 en tableta; a la izquierda para zurdos). Referencias del editor
  (`op: snap`, con `from` desde el último punto), «Poner punto» para confirmar.
- **Referencias de la persona.** El imán de la lectura (y «Más › Referencias a objetos») abre la hoja: interruptor general, «Todas»,
  «Ninguna» y cada tipo con su marca (extremo, punto medio, intersección, intersección aparente, centro, centro geométrico, cuadrante, perpendicular,
  tangente, nodo, inserción, cercano), recordados en el dispositivo (`tv.cad.referencias.v2`, `tv.cad.referencias.encendidas`). Van en
  cada `snap` como `modes` (y `limit` 4: si el editor ofrece varias en el mismo lugar, la etiqueta de la referencia dice «1 de 3» y
  tocarla pasa a la siguiente, como Tab en CAD). Con un editor que no entiende `modes` (anterior a tv-v2026.45-9, o una copia vieja
  guardada en el teléfono) se filtra en la página. Nunca se toca OSMODE: marcaría el plano «Sin guardar».
- **Entrada dinámica (la lectura).** Muestra X e Y y, con la herramienta en marcha, lo que importa: distancia y azimut desde el último
  punto (línea, polilínea, mover, copiar), radio y diámetro (círculo), ancho y alto con signo (rectángulo), ángulo (girar; con el ángulo
  escrito gira ya), distancia (desfasar; queda puesta la última, como en CAD). Tocar un valor abre el teclado propio en lugar de la
  barra de la herramienta (el plano y la punta siguen a la vista): números UTM completos, ± y punto; la punta va ahí mientras se escribe;
  el valor queda fijo (candado, punto naranja) hasta poner el punto; la × de la ficha o «Soltar» lo suelta. X/Y y los demás no se mezclan.
  El teclado del sistema no se usa (en iOS no trae signo menos y tapa medio plano).
  **Escribir nunca mueve la vista** (dueño, 17-11): ni al teclear (con UTM un valor a medias caía lejos y la vista lo seguía hasta
  el origen) ni al poner el punto. Si el punto escrito se ve, la punta va ahí; si no, un indicador en el borde, en su dirección, dice a
  qué distancia está («Ver el punto · 1.2 km»); tocarlo, y solo eso, lleva la vista ahí. Igual con distancia, azimut, radio, ancho y
  alto, ángulo y desfase.
- **Sin zoom del navegador en modo táctil** (dueño, 17-11: el doble toque acercaba la página en el iPhone): todo alrededor del área de
  dibujo lleva `touch-action: pan-x pan-y` (se desplaza, no se acerca) y el área de dibujo `none`; mientras el modo está activo el
  viewport de la página del plano no deja acercar (`maximum-scale=1, user-scalable=no`, se quita al salir) y en iOS se cancelan
  `gesturestart/gesturechange/gestureend` y `dblclick`. Las demás páginas acercan como siempre. Los campos de texto miden ≥ 16 px.
- **Atrás y el borde de la pantalla** (dueño, 17-11: deslizar desde la izquierda llevaba a la página anterior). En modo táctil, en la
  página del plano y en la sin conexión, un dedo que empieza en cualquier borde mueve el plano como cualquier arrastre; nunca va atrás o
  adelante, recarga ni hace rebotar la página. Capas, cada una para lo suyo:
  - `overscroll-behavior: none` en `html` y `body` (y `contain` en lo que se desplaza de la interfaz): sin «tirar para recargar», sin
    rebote y sin la navegación por desplazamiento horizontal de Chrome (Chrome de Android; Safari 16+ para el rebote y la recarga).
  - `touch-action: none` en el área de dibujo (`.tt-capa`), que va de borde a borde: el navegador no toma el arrastre para sí.
  - Un `touchstart` no pasivo en el área de dibujo que cancela los toques que empiezan a ≤ 28 px de un borde: el gesto de atrás/adelante
    de Safari de iOS (pestaña) no arranca. Los eventos de puntero, con los que se mueve el plano, siguen igual; en Chrome no cambia nada.
  - **La guarda del historial**, para lo que la página no puede cancelar (el gesto o botón «atrás» de Android, el de iOS en la app de
    inicio o si WebKit lo toma igual, un borde sobre las barras): mientras está el modo táctil hay una entrada más del mismo documento
    (misma dirección, `history.state.tvCadTactil`). «Atrás» la consume, la página se queda y la vuelve a poner, y cuenta como un paso
    atrás en la interfaz: cierra el diálogo, la hoja, el teclado o la ficha abiertos; si no, deja la selección por área, la herramienta,
    la selección o el puntero; si no, pregunta «¿Salir del plano?» («Salir» / «Quedarme»), y la pregunta se queda aunque se vuelva a ir
    atrás. «Salir» hace lo mismo que el botón de cerrar (con cambios sin guardar, la pregunta de siempre de `editor.js`; sin conexión, se
    guarda en el teléfono). Para salir, `TVCadTactil.ir(url)` reemplaza la entrada de la guarda con la página siguiente (`editor.js` y
    `sin-conexion.js` la usan, y también los enlaces de la página): el historial no crece y «atrás» desde la página siguiente vuelve al
    plano, que vuelve a poner la guarda. Recargar no pone otra encima. «Modo completo» la quita (`history.back()`). Chrome salta con
    «atrás» las entradas que una página puso antes de que la persona la tocara; se pone al entrar y queda válida con el primer toque.
- **Herramientas (cada figura o edición, un paso de deshacer):** polilínea, línea, rectángulo, círculo, arco por 3 puntos, punto,
  perpendicular, texto, cotas horizontal/vertical y alineada; borrar, mover, copiar, girar, desfasar (línea, polilínea, círculo, arco),
  explotar (`explode`; lo nuevo queda elegido y se dice cuántos no se pudieron), recortar y alargar (`trim`/`extend` del editor: tocar la
  parte, la punta y «Recortar aquí», o arrastrar una línea que cruce varias — `fence` —; cada vez un paso; dos dedos mueven el plano;
  «Listo» termina), capa y color; medir coordenadas,
  distancia, distancia continua, área y perímetro (por puntos o de un objeto cerrado), ángulo, longitud, info del objeto; decimales 0–4.
  Capas: actual, encender/apagar, apagar las demás, encender todas, como estaban, nueva; color de lo nuevo (ACI, por capa, por bloque).
  Modelo y presentaciones (en una presentación solo se ve y se mide).
- **Cotas y textos del tamaño de la vista** (pedido del dueño 17-11): alto ≈ 3 % del ancho visible en un teléfono (2.5 % en tableta),
  redondeado a 1, 2, 2.5 o 5 × 10ⁿ. La cota se crea con `dim_create` y su propia escala (DIMSCALE = alto / DIMTXT del estilo, `dim_style`);
  el estilo del plano y las cotas que ya había no cambian. La vista previa usa el mismo tamaño; el texto sugiere ese alto.
- **Tocar un bloque, un texto o una cota** lo elige: `query near` solo da curvas, así que sin una curva bajo el dedo se busca por caja (el
  texto por su caja; un bloque solo si hay dibujo de verdad bajo el dedo, con la referencia «Cercano» del editor que ve dentro de los
  bloques, o si es chico en la pantalla; entre varios, el más chico).
- **Un plano que solo se mira nunca queda «Sin guardar».** La revisión de partida se toma cuando el editor terminó de abrir (respuesta de
  `open` y revisión quieta), no al ver aparecer su pestaña: en un plano grande la página daba el plano por cargado a media apertura (y la
  capa táctil empezaba a pedirle cosas al editor). `TVCad.registro()` (y la consola, al pasar a «Sin guardar») dice qué le pidió la
  página al editor justo antes, para diagnosticar en el teléfono. La primera vez que un plano pasa a «Sin guardar» en una sesión, ese
  registro (solo nombres de operaciones, tiempos, si se aceptaron, revisiones y el modo táctil o completo; nada del dibujo) va una vez
  con el siguiente borrador en `X-TV-Registro`; el servidor lo valida y recorta (`cad/registro.py`, ≤ 2 000 caracteres) y lo escribe
  en el journal (INFO, `itt_herramientas.cad`, «cad registro sin guardar plano=… cuenta=…»). Nunca rompe la subida del borrador.
- **Ver todo** (al abrir, tres toques, «Más › Ver todo el plano») encuadra con un 6 % de margen la extensión que da el editor
  (`extents` con `space: "current"`, desde tv-v2026.45-11, con las reglas de CAD: solo lo dibujado, las capas congeladas no cuentan, ni
  los centros de arcos casi rectos que caen a kilómetros). Con un editor sin esa op, la página suma las cajas de los objetos del modelo
  (sin las capas congeladas).
- **Lo que depende del editor.** Al entrar se mira qué ops tiene (`state.capabilities` o una petición que solo se rechaza por sus datos):
  sin `explode`, `trim`, `extend` o `dim_create` esas herramientas no se muestran y las cotas se dibujan como antes.
- **Hoja numérica** para el texto, la altura del texto y el nombre de una capa.
- **Área de dibujo siempre oscura** (decisión del dueño): lo claro del plano se lee bien; la interfaz alrededor sigue el tema de la cuenta.
- **El marco del editor** en modo táctil: solo el área de dibujo, con la opción «solo lienzo» del editor (op `canvas_only`, o
  `?canvas=1` en la página sin conexión; build tv-v2026.45-6). Es un estado de la sesión que el editor no guarda: el editor completo del
  mismo dispositivo queda como estaba. Ocultar sus barras con órdenes (MENUBAR, FILETAB, COMMANDLINEHIDE) sí quedaría guardado: no se usa.
  Al soltar un gesto, `view_set` (centro y altura en f64) deja la vista donde se ve.

## CAD sin conexión (teléfono) — detrás de `TV_CAD_TACTIL`

- **El editor, una vez por versión.** «Más › Sin conexión › Descargar CAD en este teléfono» (≈ 39 MB con compresión, 123 MB guardados; sin las letras chinas, japonesas y coreanas, que se bajan solas con conexión si un plano las usa),
  solo en la app instalada (iOS borra lo de un sitio no instalado a los 7 días sin uso) y con `navigator.storage.persist()`. Cache Storage
  `itt-cad-editor-<versión>`; `/cad/tactil/paquete/` da la lista (ruta, SHA-256, tamaño y tamaño comprimido, del `MANIFIESTO.json`).
  Al cambiar la versión fijada se baja solo lo que cambió (lo de igual SHA-256 se copia de la versión anterior) y la anterior se borra. La
  versión instalada se lee de la propia caché: también sin conexión. Las páginas de campo que no usan CAD nunca bajan el editor.
- **El service worker** (`offline/sw_cad.js`, agregado por `sw.py` solo con el ajuste) sirve el editor descargado, abre la página sin
  conexión al navegar a `/cad/…` sin señal, sus estilos y scripts desde `itt-cad-pagina` y el panel de Civil desde `itt-cad-pagina-civil`.
  Nunca guarda planos ni páginas con datos; esas cachés las llena la página.
- **Con conexión, desde el teléfono**: abrir un plano «Disponible sin conexión» no lo baja (ver «Datos móviles»). La cabecera dice
  «Desde el teléfono»; si había una versión nueva, se baja una vez, reemplaza la copia y se dice: «Había una versión nueva (la N): se
  descargó y quedó en el teléfono.» Con cambios, una copia de ellos queda en el teléfono a los 2 s (a lo más cada 10 s): si la app se
  cierra, al volver se ofrecen como «Tienes cambios sin guardar del …» y recuperarlos no baja nada; la página sin conexión también los abre.
- **«Disponible sin conexión» por plano** (base `tv-cad-campo` de IndexedDB, de la cuenta): llevarlo **no** toma el bloqueo. Sin conexión
  se ve, se mide y se dibuja en `/cad/sin-conexion/`; lo cambiado queda como «cambios en el teléfono», nunca una versión. Con conexión:
  versión nueva solo si la del servidor sigue siendo la base y nadie tiene el bloqueo; si no, una copia. El servidor lo exige igual.
- **Perfiles corporativos**: los planos son una sola biblioteca de la empresa para los dos perfiles (desde 1.2.17 solo las cotizaciones, los
  reportes y los croquis son de un perfil). Las copias del teléfono son de la cuenta (cada registro lleva `cuenta` y solo se leen los de
  la cuenta con sesión), igual en cualquier perfil.
- **Cerrar sesión**: las copias de planos se borran; los cambios sin subir no se pierden sin avisar (se ofrece descargarlos; si se sale
  igual, quedan ocultos en el teléfono hasta que vuelva a entrar la misma cuenta). Con la sesión cerrada la página sin conexión no muestra
  nada. Al quitar el CAD del teléfono se borra el editor; un plano con cambios sin subir no se puede quitar.

## Datos móviles (17-11-2026)

Pedido del dueño: con la app instalada, el CAD descargado y un plano «Disponible sin conexión», usar ese plano con conexión (datos móviles)
no debe gastar datos. **Objetivo: ≤ 30 KB por apertura (abrir y cerrar) y ≤ 2 KB por minuto abierto**, sin importar el tamaño del plano
ni del editor. Sin conexión, 0 bytes.

**Cómo se mide** (`pruebas_navegador/test_cad_datos_moviles_navegador.py`, permanente): la app instalada en un teléfono (perfil que se
guarda, 375 × 812, toque) habla con el servidor de pruebas solo a través de `contador_bytes.ContadorBytes`, un proxy que cuenta los bytes
en el cable en los dos sentidos por petición (método, ruta, estado, bytes pedidos y recibidos). Hace lo que Caddy en producción: gzip de lo
comprimible, el `.gz` del editor, `immutable` para los estáticos con huella. Cuenta HTTP/1.1 con todas sus cabeceras (~1.3–1.7 KB por
petición, la cookie incluida): en producción va HTTP/2 o HTTP/3, que comprime las cabeceras repetidas, así que lo de allá es menos.
Playwright (`request.sizes()`, `response.from_service_worker`) lo cruza. Chromium y WebKit; plano DXF sintético de 1.2 MB con dos
presentaciones con ventanas. La prueba falla si (b), (c), el reabrir de (e) y de (f) pasan del objetivo, o si (g) manda algo.
`ITT_DATOS_CAD=<carpeta>` deja la tabla medida (JSON y Markdown, con el detalle por petición).

| Escenario | Antes · Chromium | Antes · WebKit | Después · Chromium | Después · WebKit |
| --- | ---: | ---: | ---: | ---: |
| (a) Primera vez: bajar el CAD y llevar el plano | 78.6 MB | 110.1 MB | 45.9 MB | 108.8 MB |
| (b) Abrir con conexión + cerrar | 1.2 MB | 1.2 MB | 23.4 KB | 25.9 KB |
| (b) Por minuto abierto (2 min mirando) | 3.3 KB | 3.0 KB | 1.6 KB | 1.5 KB |
| (c) Reabrir + cerrar | 1.2 MB | 1.2 MB | 23.4 KB | 25.9 KB |
| (c) Por minuto abierto | 3.3 KB | 3.0 KB | 1.6 KB | 1.5 KB |
| (d) Abrir | 1.2 MB | 1.2 MB | 20.7 KB | 24.4 KB |
| (d) 5 objetos en 3 min (borradores y latidos) | 2.0 MB | 2.0 MB | 113.9 KB | 122.7 KB |
| (d) Guardar la versión | 4.1 MB | 4.1 MB | 107.8 KB | 118.1 KB |
| (e) Primera apertura con el editor nuevo | 33.6 MB | 33.6 MB | 38.2 KB | 40.2 KB |
| (e) Reabrir + cerrar | 2.1 MB | 2.1 MB | 22.4 KB | 26.0 KB |
| (f) Abrir con la versión de otra cuenta + cerrar | 4.1 MB | 4.1 MB | 2.1 MB | 2.1 MB |
| (f) Reabrir + cerrar | 2.1 MB | 2.1 MB | 22.4 KB | 26.1 KB |
| (g) Modo avión: abrir, mover, acercar, presentación | 10.4 KB | 0 B | 0 B | 0 B |
| (h) Plano NO llevado: abrir + cerrar | 1.2 MB | 1.2 MB | 1.2 MB | 1.2 MB |
| (h) Por minuto abierto | 3.3 KB | 3.0 KB | 1.6 KB | 1.5 KB |

Medido el 17-11-2026 (antes: `origin/web` 52b0bcc; después: esta rama), HTTP/1.1, KB = 1024 bytes. «Abrir + cerrar» incluye la búsqueda
de versión del service worker que el navegador hace por su cuenta después de abrir la página (una por página). El plano pesa 1.2 MB (2.1 MB
desde que el editor lo guarda en (d)). Notas:
- **(a)** es una sola vez y el editor es casi todo: Chromium 46 MB; WebKit 109 MB porque su primera carga del editor pide el `.wasm`
  principal dos veces (el cargador del editor) y la descarga no reusa lo que su navegador ya tenía (tres veces 33 MB). Queda para el
  editor; la hoja dice «Mejor con wifi».
- **(d)** el borrador y el guardado son el plano con gzip: 2 MB → ~110 KB (DXF). Un DWG casi no se comprime.
- **(e)** de 33.6 MB a ~40 KB: solo el archivo que cambió (1 KB), la lista del paquete y la página sin conexión otra vez.
- **(f)** la versión nueva se baja una vez (2.1 MB, antes dos veces) y luego vuelve a ~25 KB.
- **(g)** antes, en Chromium, salía la búsqueda de versión del service worker (Playwright no la corta con `set_offline`); ahora la red se
  corta también en el proxy y no llega nada en ninguno de los dos.

**Qué gastaba y por qué** (antes):
- **El plano se bajaba entero en cada apertura** (1.2 MB del de prueba): la página del plano siempre pedía `/cad/<id>/archivo/`; la copia
  del teléfono solo la usaba la página sin conexión.
- **Y otra vez después**: al abrir, `campo.js` pedía `estado` (con la lista de versiones) para poner al día la copia, y tras cada guardado o
  versión nueva la bajaba de nuevo entera ((d) y (f): el doble del plano).
- **La página sin conexión se volvía a guardar en cada apertura** (su HTML, el manifiesto y `perfil.css`, ~12 KB).
- **La página del plano pesaba ~30 KB comprimida**, más de la mitad el panel de Civil, que es igual para todos los planos.
- **El service worker en cada página** (~17 KB en WebKit, ~5 KB en Chromium): al abrir cada página de la app el navegador vuelve a pedir
  `sw.js` para ver si hay versión nueva. `sw.js` no tenía ETag (nunca era un 304) y WebKit, además, pide entero, sin preguntar si cambió,
  `sw.js` y los tres scripts que importa (la cola, tus y el subidor).
- **Latido cada 30 s** (~1.7 KB cada uno en HTTP/1.1): 3.3 KB por minuto solo por tener el plano abierto.
- **Borradores**: cada 2 minutos mientras había cambios, aunque no hubiera nada nuevo desde el anterior, y sin comprimir (un DXF de 2 MB).
- **Editor nuevo**: el marco abría la versión nueva entera de la red (33 MB) mientras, aparte, se bajaba solo lo que cambió.
- **Primera vez**: el editor llegaba dos veces (el marco y la descarga, que pedía todo sin usar lo que el navegador ya tenía) y el plano
  también (al abrirlo y al llevarlo).
- **Sin conexión** no salía nada de la página; lo único que se veía en el cable era la búsqueda de versión del service worker, que hace el
  navegador por su cuenta (Playwright no la corta con `set_offline`; en modo avión de verdad falla sin costo). La prueba ahora corta
  también la red en el proxy.

**Qué hace ahora** (con conexión, plano en el teléfono):
- **El plano no se baja.** `editor.js` pregunta `GET /cad/<id>/archivo/` con `If-None-Match: "<SHA-256 de la copia>"` (el teléfono lo
  calcula de su copia; `ETag` de la versión es su SHA-256): 304 sin cuerpo y se abre la copia del teléfono, con «Desde el teléfono» en la
  cabecera. Si hay otra versión, llega una vez (200), reemplaza la copia y se dice «Había una versión nueva (la N): se descargó y quedó
  en el teléfono.» La respuesta del servidor manda: nunca se abre una versión vieja sin decirlo. La pregunta pide la misma sesión y el
  mismo rol que bajar el plano. Una versión restaurada con el mismo archivo es 304 con su número nuevo.
- **Guardar** una versión deja esos mismos bytes como la copia del teléfono (no se baja nada después) y sube el plano una vez, con gzip
  si conviene. **Llevar** un plano al teléfono desde su página usa los bytes recién abiertos.
- **Borradores**: la copia de los cambios queda en el teléfono a los 2 s (sin red); el borrador del servidor sale solo si algo cambió desde
  el anterior, a lo más cada 2 minutos y al pasar la app a segundo plano, con gzip si conviene (un DXF baja a una décima parte; un DWG ya
  viene comprimido). Al cerrar la app, lo último está en el teléfono y se ofrece al volver sin bajar nada. Delta no: un DWG cambia por
  secciones comprimidas y el editor reescribe el archivo entero al guardar, así que un parche no sería más chico que el gzip y sería frágil.
- **Latido** cada 60 s (antes 30) y bloqueo de 150 s (antes 90), como un navegador deja latir una pestaña en segundo plano; quien solo mira
  y quien vigila un plano ajeno usan `estado?ligero=1` (sin la lista de versiones).
- **La página**: el panel de Civil (~16 KB comprimidas, igual para todos) llega aparte una vez por versión (docs/civil.md); la página sin
  conexión se vuelve a guardar solo cuando cambia su huella (`tactil.pagina`, en la configuración de la página del plano); el editor nuevo
  se baja **antes** de abrir el marco y solo lo que cambió, y la primera descarga usa lo que el navegador ya trajo al abrir el plano
  (Chromium; WebKit no lo reusa).
- **El service worker** (docs/pwa.md): `sw.js` lleva `ETag` (Chromium recibe un 304), se sirve sin sus líneas de comentario y solo importa
  la cola, tus y el subidor donde hay Background Sync (Android): WebKit, que no lo tiene, los bajaba enteros en cada página.

Lo que sigue viajando en cada apertura (lo que debe ir al servidor): la página del plano (sus datos: versión vigente, bloqueo, borrador),
la pregunta de versión (304), tomar y soltar el bloqueo, `perfil.css` (los colores del perfil, red primero) y, si el navegador la busca, la
versión del service worker (304; WebKit pide `sw.js` entero, ~6 KB, y también el manifiesto). Por minuto: un latido.

## Espacio, archivos que faltan y autoría

- **Espacio:** CAD tiene su propia reserva de 20 GB, aparte de los 12 GB de Herramientas (Portal, `docs/arquitectura.md` §9.1 y §13). La
  biblioteca usa **18 GiB** de ella (`cad/servicio.py`, `LIMITES["biblioteca_bytes"]`: versiones vigentes y anteriores, papelera y
  borradores) y deja el resto a salidas (PDF, conversiones, ZIP) y temporales, para que tv-salud no quede en «crítico» con la biblioteca
  llena. Al llenarse se recortan las versiones más viejas (siempre quedan `VERSIONES_MINIMAS` = 5 por plano); si aun así no cabe, se dice
  claro. El recorte va dentro de la transacción del guardado, después de comprobar bloqueo y versión, y de uno en uno (candado de
  PostgreSQL): un guardado rechazado no borra nada, restaurar nunca recorta la versión que se restaura y los archivos se borran al
  confirmar. Un borrador de recuperación nunca recorta versiones: si no cabe, se niega.
  CAD **no** cuenta en la cuota de 5 GB por cuenta de Herramientas (`TV_HERRAMIENTAS_CUOTA`), que solo mide las fotos de campo.
- La biblioteca muestra «Planos: x de 18 GB» (aviso desde el 90 %); quien administra ve además el desglose.
- **Sin respaldo:** por decisión del propietario (15-11-2026) los planos no van al respaldo en R2 (`/srv/tresvizo/herramientas/cad` está
  excluido). Tras restaurar o cambiar de servidor puede haber renglones sin archivo: la lista los muestra como «Archivo no disponible», el
  plano lo explica en vez de abrir el editor, se pueden eliminar y nunca hay un error. `manage.py cad_reconciliar` (sin argumentos;
  `--simular` opcional; idempotente) marca las versiones sin archivo (`VersionPlano.sin_archivo`), desmarca las que volvieron y quita los
  borradores sin archivo; `tvctl cad-reconciliar` lo corre. También cuenta lo que hay en el disco sin renglón (carpetas y archivos de más
  de una hora, p. ej. tras restaurar la base a un punto anterior con el disco intacto) y solo lo borra con `--borrar-huerfanos`: puede ser
  la única copia de versiones posteriores, así que ni el comando sin argumentos ni la limpieza diaria lo tocan.
- **Autoría:** la lista dice quién creó cada plano y quién lo guardó y cuándo; el plano, su línea de guardado y creación; cada versión,
  «Guardada por … · fecha».

## Carpeta del proyecto en esta computadora (imágenes, ortofotos y referencias)

Como en AutoCAD, Civil 3D y MicroStation, el plano guarda **referencias** (IMAGE con su IMAGEDEF, xrefs) y nunca los píxeles. Las
ortofotos (de más de 3 GB), las fotos de fachadas y cortes (PointCab exporta un DWG con cientos de imágenes) y los dibujos referidos se
quedan en la computadora. El servidor guarda solo el DWG, con las rutas tal como estaban.

- **Vincular** (botón «Referencias» en la cabecera del plano, o el aviso «Vincular la carpeta del proyecto» cuando el plano usa archivos
  que no están): en Chrome y Edge (Windows y Mac) la persona elige la carpeta (`showDirectoryPicker`, solo lectura); el navegador la
  recuerda por cuenta y plano (IndexedDB `tv-cad-carpetas`, clave `<cuenta>:<plano>`) y, tras reiniciar, la página dice «El navegador
  pide permiso para leerla» con un solo botón «Permitir el acceso». Se puede **agregar otra carpeta** (p. ej. las imágenes en otro disco
  y las referencias en la red): se busca en todas. **Safari y Firefox** no tienen esa función: la carpeta se elige (o se suelta sobre el
  panel) cada vez que se abre el plano y vale mientras la página esté abierta; el panel lo dice.
- **Resolver**: cada ruta del plano (absoluta de Windows `C:\…`, de red `\\servidor\…`, de Mac o relativa `..\Fotos\…`) se busca
  en la carpeta por la ruta relativa más larga que coincida, sin distinguir mayúsculas; si solo coincide el nombre y hay un único archivo
  con ese nombre, por nombre. Si varios empatan, se usa el primero (orden alfabético) y el panel dice «Varias con ese nombre» con los
  demás. Reglas y pruebas: `static/herramientas/cad/local/carpeta.js`, `pruebas/js/cad_local_carpeta.mjs`.
- **Nada se sube**: ni píxeles, ni nombres de archivos (fuera de lo que el plano ya trae), ni miniaturas, ni métricas. La página lee los
  archivos con `File.slice` y los decodifica en sus trabajadores; al editor le pasa los píxeles, por mosaicos, con sus funciones
  `ocs_image_tiles_open` y `ocs_image_tile` (mismo origen, dentro de la página). Lo prueban `test_cad_local_navegador.py` (registro de cada petición de la página, sus marcos y
  trabajadores: ningún cuerpo lleva bytes de la carpeta ni sus nombres) y el contador de bytes.
- **Sin la carpeta** (otra computadora, otra cuenta): cada imagen se ve como su marco con el nombre del archivo (parche 19 del editor) y
  arriba del plano un aviso con una sola acción, «Vincular la carpeta del proyecto».
- **Panel «Referencias externas»**: cada imagen y cada dibujo referido con «Encontrada», «Encontrada por nombre», «Varias con ese nombre»
  o «No encontrada», el archivo que se usó, su tamaño y sus píxeles; «Volver a buscar», «Agregar otra carpeta», «Desvincular». Por
  imagen: «Enviar atrás», «Ocultar»/«Mostrar», «Transparencia», «Recortar» (dos esquinas en el dibujo, IMAGECLIP del editor) y «Quitar
  del plano» (quita las imágenes de ese archivo y su IMAGEDEF; el archivo sigue en la computadora). Con cientos de imágenes el panel
  lista las que faltan y las ambiguas, y cuenta las encontradas en una línea. El marco de todas (IMAGEFRAME) se cambia en el editor.
- **Guardar**: las rutas quedan como la persona las tenía (compatibles con AutoCAD); nada se reescribe a rutas del navegador.

### Con varias pestañas

Cada pestaña es un plano con **su** carpeta (la clave del navegador es `<cuenta>:<plano>`), su índice y su panel «Referencias externas»;
el panel, el aviso y «Insertar imagen georreferenciada» son siempre de la pestaña a la vista.

- **Dos proyectos con los mismos nombres.** Dos proyectos suelen repetir nombres (`C:\Obra\Fotos\f001.png`, `base.dwg`). El editor
  guarda los mosaicos de una imagen **por documento y ruta**, atados al archivo exacto que la página resolvió: su carpeta, su ruta en
  ella, su nombre, tamaño y fecha (parche 60). Un plano dibuja solo lo que él ató: una pestaña sin carpeta muestra el marco con el
  nombre, nunca la imagen de otro proyecto, aunque los dos archivos se llamen y pesen igual. Si dos planos usan el mismo archivo de la
  misma carpeta (hojas del mismo proyecto), comparten sus mosaicos y no se vuelven a leer. El editor nunca busca una imagen «por el
  nombre» (parche 58).
- **Dibujos de referencia**: se dan al documento de su plano (`ocs_xref_supply(ruta, bytes, document_id)`): la xref de otra pestaña con el
  mismo nombre no se vuelve a cargar con el archivo de este proyecto (parche 58).
- **Atrás**: una pestaña que pasa atrás se queda solo con sus niveles gruesos (de hasta 1024 px por lado), hasta 256 MB entre todas,
  para volver sin esperar; lo decide el editor. **Cerrar** una pestaña suelta todo lo suyo (sus mosaicos, sus lecturas y pirámides en
  curso); la carpeta sigue recordada para ese plano.
- **Permiso tras reiniciar**: al recargar vuelven las pestañas, cada una con su carpeta recordada; la de la vista dice «El navegador pide
  permiso para leerla» y **un solo toque** pide el permiso de las carpetas de todas las pestañas que lo esperan (una carpeta que comparten
  varios planos, una vez). Si el navegador no concede alguna en ese toque, esa pestaña lo vuelve a pedir al mostrarse.
- **Guardar todo** guarda en cada versión los IMAGE e IMAGEDEF de su plano, con la ruta relativa a su carpeta; nunca el nombre de la
  carpeta de la computadora.
- **Esc** con «Referencias externas», «Insertar imagen georreferenciada», «Transparencia» o la confirmación abiertos: el primero cierra la
  hoja (la orden sigue), el siguiente es del editor.
- **Nada viaja** al cambiar, abrir o cerrar pestañas: lo prueba `test_cad_pestanas_local_navegador.py` con el registro de cada petición
  (ningún cuerpo lleva bytes de las carpetas ni sus nombres) y el contador de bytes en el cable (`contador_bytes.py`).

### Insertar imagen georreferenciada

«Más acciones» → «Insertar imagen georreferenciada» (o el botón del panel). La persona elige la imagen (y, sin acceso a carpetas, también
su archivo de mundo). Formatos: TIFF/GeoTIFF/BigTIFF, JPEG, PNG, BMP, GIF y WebP; archivos de mundo `.tfw`, `.tifw`, `.jgw`, `.jpgw`,
`.pgw`, `.pngw`, `.wld` (y en mayúsculas), buscados junto a la imagen en la carpeta vinculada.

- **Archivo de mundo**: las seis líneas A, D, B, E, C, F, con rotación; C y F son el **centro** del píxel superior izquierdo (convención
  de ESRI, que siguen GDAL y AutoCAD Map 3D / Raster Design). El IMAGE de AutoCAD se coloca por la **esquina** inferior izquierda de la
  imagen (DXF: código 10 = punto de inserción, esquina inferior izquierda; 11 y 12 = vectores U y V de un píxel). Por eso:
  inserción = (C − A/2 + (alto − ½)·B, F − D/2 + (alto − ½)·E), U = (A, D), V = (−B, −E). Es el cálculo de `io/georef.rs` del editor
  (con pruebas de cada esquina de una imagen girada).
- **GeoTIFF**: ModelTransformation, o ModelPixelScale con ModelTiepoint, o tres o más tiepoints ajustados por mínimos cuadrados; con
  RasterPixelIsArea (lo normal) el punto (0, 0) es la esquina del primer píxel, con RasterPixelIsPoint es su centro (GeoTIFF 1.0
  §2.5.2.2; como GDAL por omisión).
- **Unidades**: la georreferencia se toma en metros y se pasa a las unidades del plano (INSUNITS: mm ×1000, cm ×100, pies…; sin
  unidades o en metros, tal cual).
- **Sistema de coordenadas**: si el GeoTIFF trae su EPSG y el GEODATA del plano nombra otro, avisa una vez («La imagen está en
  EPSG:32613 y el plano en EPSG:32614…»).
- **Sin georreferencia**: punto de inserción (esquina inferior izquierda), ancho y rotación, como IMAGEATTACH (vienen prellenados con el
  centro y la mitad del ancho de la vista).
- Se crea un IMAGE estándar (RasterImage + IMAGEDEF en el diccionario de imágenes), en la capa «Imágenes» (se puede cambiar) y, por
  omisión, **debajo del dibujo**. La ruta que se guarda es la relativa a la carpeta del proyecto (`Ortofotos\orto.tif`, con barras de
  Windows: AutoCAD la resuelve si el DWG se guarda en esa carpeta); si la imagen no está en la carpeta, su nombre, y la página ofrece
  vincular también la carpeta de la imagen. Op del editor: `image_attach`.
- **Entrada para otros módulos de la página** (estable): `TVCadLocal.insertarImagen(archivos)`, con `archivos` una lista de `File` (la
  imagen y, si hay, su archivo de mundo), abre la misma hoja que el menú (lugar, capa, «Insertar») y sigue el mismo camino.

### Ortofotos enormes (≥ 3 GB): solo lo que la vista necesita

El editor web es wasm32 (4 GB de memoria) y la GPU tiene límites de textura: una ortofoto nunca se carga entera.

- **Arquitectura: mosaicos y niveles, como un SIG (parche 60 del editor, 18-11-2026).** El editor dibuja la imagen como un IMAGE normal
  (su lugar, su recorte, su orden, su desvanecido, rotación de la vista, coordenadas UTM sin temblor), y sus píxeles son una
  **pirámide de mosaicos**: el nivel 0 es el archivo, el nivel z el archivo reducido 2^z veces, en mosaicos de 512 px desde la esquina
  superior izquierda (con un píxel de más en cada lado que tiene vecino, para que el filtrado no deje líneas entre mosaicos); el nivel
  más alto es un solo mosaico, la **raíz**, que se lee primero y está siempre: la imagen se ve entera desde el principio. Las imágenes
  de hasta 1024 px por lado (fachadas) son un mosaico por nivel, de su tamaño a 64 px.
  - **El editor decide** qué necesita la vista (`image_tiles`): el nivel cuyos píxeles miden hasta un píxel de la pantalla (con la
    escala de Windows de 1.25 o 1.5 incluida) y sus mosaicos visibles, del centro hacia afuera; luego un anillo de un mosaico
    alrededor, el nivel siguiente más grueso y, para el centro de la vista, el siguiente más fino. Dibuja lo que tiene: el mosaico; si
    falta, sus cuatro hijos más finos si están todos (al alejarse); si no, la parte de su **ancestro más cercano**, ampliada (al
    acercarse se ve el nivel anterior y luego se afina). Nunca dos piezas encima de la otra (el desvanecido y la transparencia se ven
    bien), y cada vértice se calcula en f64 y se parte en dos f32 por separado (parche 59): los mosaicos vecinos comparten sus bordes
    bit a bit, sin costuras a ningún acercamiento, escala de pantalla ni giro de la vista.
  - **La página lee y entrega**: del archivo, en sus trabajadores, a lo sumo dos lecturas por trabajador; las lecturas de mosaicos que
    salieron de la vista se cortan; los mosaicos de una misma fila y nivel van al mismo trabajador (comparten los bloques del TIFF);
    al editor se le dan a lo sumo 4 por cuadro, para que ningún cuadro se alargue. Pregunta al editor a lo sumo cada 90 ms mientras
    hay algo que leer y cada 250 ms con la vista quieta (cada pregunta lo despierta: sin esa pausa le quitaría la CPU al dibujo). Un mosaico se lee solo de una fuente al menos tan
    fina como su nivel (las vistas internas del archivo, la pirámide local o el nivel completo, si leerlo cuesta poco): nunca se le da
    al editor algo borroso como si fuera nítido; mientras no hay fuente, el editor dibuja el ancestro.
  - **Lo ya visto queda**: un mosaico leído se queda en el editor (y su textura en la tarjeta de video): volver a pasar por ahí es
    nítido al instante y no lee nada.
  - Se eligió así, y no componer en la página una imagen de la pantalla, porque esa imagen habría que volver a subirla entera a la
    tarjeta de video en cada paso (era el «borroso y luego nítido» de la versión 1.2.19); y no un lienzo de la página bajo el editor,
    porque la imagen sigue siendo una entidad del editor (orden de dibujo con el resto, recortes, bloques y xrefs, selección) y nunca se
    desfasa de la vista.
- **Lectura por partes**: TIFF y BigTIFF en mosaicos o en tiras, leyendo solo los bloques de la ventana (`File.slice`), en el nivel
  adecuado: las vistas generales internas del archivo (COG o TIFF piramidal) si las tiene. Compresión: ninguna, LZW, Deflate, PackBits y
  JPEG en TIFF (YCbCr o RGB, con JPEGTables); predictor horizontal; 8 bits (16 se reducen a 8); gris, RGB, RGBA y paleta; un plano o
  planos separados. **No soportado** (el panel lo dice): JPEG antiguo (compresión 6), fax (CCITT), muestras flotantes (modelos de
  elevación), CMYK y Lab, y JPEG/PNG de más de 268 millones de píxeles (conviértelos a GeoTIFF en mosaicos).
- **Mientras se arma la pirámide local, ella va primero**: tiene su trabajador para ella sola (las lecturas de mosaicos van a los
  demás), del archivo completo solo se leen los mosaicos que se ven de los niveles completo y ½ (pocos bloques del archivo), sin anillo
  ni niveles vecinos; los niveles intermedios esperan a la pirámide y el editor dibuja mientras tanto el nivel ya armado más cercano.
  Los trabajadores vuelven a leer su manifiesto (a lo sumo cada segundo) y usan cada nivel en cuanto queda listo.
- **Sin vistas generales** (o JPEG/PNG de más de 16 millones de píxeles): la página arma una **pirámide local una sola vez**, en segundo
  plano, en el almacenamiento privado del navegador (OPFS, carpeta `tv-cad-piramides`, por nombre + tamaño + fecha del archivo): mosaicos
  de 256 px en JPEG (PNG si hay transparencia) a ½, ¼, ⅛… y una vista general de 2048 px que se va llenando mientras se lee el archivo
  (la imagen aparece de arriba abajo: los mosaicos del editor que salen de ella se le dan con cada avance, solo los de las filas que
  cambiaron). Avanza por bandas: se puede «Detener» y «Continuar» (y se retoma sola al volver a abrir), y
  «Borrar vistas generales» en el panel las quita de este navegador. Nunca sale de la computadora.
- **Memoria** (límites fijos):

  | Dónde | Qué | Límite |
  | --- | --- | --- |
  | Editor | mosaicos del plano a la vista | 512 MB; sale primero el usado hace más tiempo, nunca las raíces del plano a la vista ni lo que se dibujó o pidió en los dos últimos pasos |
  | Editor | planos de atrás | solo sus niveles de hasta 1024 px, 256 MB entre todos (sale primero el que se vio hace más tiempo) |
  | Editor | límite duro | 1.5 GB: un mosaico que no cabe se rechaza |
  | Tarjeta de video | texturas de mosaicos | cada una con su nivel y uno a la mitad; las que no se usan se quedan hasta 256 MB mientras su mosaico esté en el editor |
  | Página | trabajadores (2 o 3) | caché de bloques del TIFF de 128 MB por trabajador y 400 mosaicos de la pirámide local abiertos |
  | Página | por entregar | lo leído espera a lo sumo unos cuadros (4 mosaicos por cuadro) |
- **Imprimir**: la impresión a PDF corre en el servidor, que no tiene los píxeles: las imágenes salen como su marco y su nombre, y la hoja
  de «Imprimir PDF» lo dice. (El editor web no imprime PDF.)

**Medido** (19-11-2026, Mac M4, Chromium de Playwright con dibujo por software, `test_medir_ortofoto` de
`test_cad_mosaicos_navegador.py` con `TV_CAD_MEDIR`; la misma prueba, que solo mira la pantalla, con la página y el editor de 1.2.19
(`tv-v2026.45-12`) y con los de mosaicos). Patrón de nitidez (`cad_local_rasters.patron_nitidez`: ajedrez de 1 px y de 8 px), vista a
2 px de pantalla por píxel del archivo. «Nítido»: se ve el ajedrez de 1 px (el nivel completo, sin remuestrear); «¼»: se ve el de 8 px.

| Archivo | Versión | Al abrir: ¼ / nítido | Mover a terreno nuevo | Volver a lo ya visto | Acercar un paso | Cuadros al mover y acercar sin parar (mediana / p95 / máx.) | Pico del navegador | En el editor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BigTIFF 3.12 GB, 32 256 px, mosaicos de 512, sin compresión **ni vistas generales** | 1.2.19 | 4.9 s / nunca | ¼ 0.22 s / nunca | ¼ 0.26 s / nunca | nunca | 16.7 / 33 / 34 ms | 2.3 GB | 18 MB |
| ídem | mosaicos | 3.7 s / 3.7 s | 0.47 s | 0.09 s | ¼ 0.06 s / 0.23 s | 16.7 / 33 / 50 ms | 2.3 GB | 49 MB |
| JPEG en TIFF 0.67 GB, 32 768 px, mosaicos de 512, **6 vistas internas** | 1.2.19 | 3.5 s / nunca | nunca | nunca | ¼ 0.06 s / nunca | 16.7 / 33 / 50 ms | 1.7 GB | 6 MB |
| ídem | mosaicos | 3.4 s / 3.4 s | 0.29 s | 0.06 s | 0.03 s | 16.8 / 50 / 50 ms | 2.3 GB | 99 MB |

- «Nunca» es dentro de 2 min (10 min al abrir): el detalle de 1.2.19 se remuestreaba a una ventana fraccionaria y promediaba los píxeles
  sueltos; con ortofotos reales eso se ve como el «borroso y luego nítido» del dueño. Los mosaicos copian los píxeles tal cual.
- Mover y acercar: el anillo y el nivel siguiente ya leídos hacen que casi siempre esté al instante; volver no lee nada.
- **Pirámide local** (BigTIFF de 1.2 GB, 19 968 px, sin vistas generales; `test_medir_piramide`, dos corridas de cada una, con la vista
  sobre la imagen recorriéndola cada 2 s y fuera de ella):

  | Versión | Vista | Pirámide completa | Cuadros mientras se arma (mediana / p95 / máx.) |
  | --- | --- | --- | --- |
  | 1.2.19 | fuera | 11.1 s, 11.2 s | 16.7 / 16.7 / 67 ms |
  | 1.2.19 | sobre | 12.6 s, 12.6 s | 16.7 / 16.8 / 50 ms |
  | mosaicos, primera entrega (`e90915e`) | fuera | 103 s, 93 s | 16.7 / 33 / 183 ms |
  | mosaicos, primera entrega | sobre | 100 s, 100 s | 16.7 / 33 / 217 ms |
  | mosaicos | fuera | 11.5 s, 11.6 s | 16.7 / 16.8 / 100 ms |
  | mosaicos | sobre | 13.5 s, 13.3 s | 16.7 / 16.8 / 167 ms |

  La primera entrega preguntaba al editor sin pausa (la espera entre vueltas se perdía y volvía a preguntar enseguida): cada pregunta
  lo despierta y lo redibuja, y con el dibujo por software eso le quitaba la CPU al trabajador de la pirámide (9 veces más lenta) y
  alargaba los cuadros. Ahora espera 90 o 250 ms entre vueltas, y la pirámide va primero (arriba).
- Nada se sube: 23 KB en toda la medición (latidos), ningún byte ni nombre del archivo. Los archivos se generan para medir y se borran al
  terminar.

### Planos tipo PointCab (cientos de imágenes y referencias)

- Con la carpeta: todas aparecen, cada una a la resolución con que se ve (de 64 px si es pequeña en pantalla a la completa al acercarse):
  las de hasta 1024 px por lado van como una imagen entera por nivel, sin mosaicos ni niveles vecinos de más; las más grandes, por
  mosaicos como las ortofotos. La memoria depende de la pantalla y no del número de imágenes; las más grandes en pantalla primero.
- Sin la carpeta: marcos con nombres, al instante, y un solo aviso para vincular.
- **Medido** (mismo equipo): 240 imágenes PNG/JPG en 8 carpetas con su referencia externa: todas visibles 0.8 s después de vincular,
  1 imagen = la resolución con que se ve (al ver todo, 2.9 MB de píxeles en el editor para las 240), 0 bytes de la carpeta subidos.
- La estructura exacta de las exportaciones de PointCab no está publicada en detalle; el juego de prueba sigue lo habitual (el DWG con
  las imágenes en carpetas por vista y rutas absolutas de Windows). Comprobar con una exportación real del propietario (sin subirla).
- **Referencias a dibujos** (xref adjunta o superpuesta): se leen de la carpeta y se muestran de solo lectura, como AutoCAD (sin «unir»).
  Las anidadas se resuelven al cargar su padre, hasta 4 niveles.
- Juego de prueba sintético (nunca datos de clientes): `pruebas_navegador/cad_local_rasters.py pointcab DIR --imagenes 320` crea un DXF
  con IMAGE en fachadas y cortes (rutas absolutas de Windows como las exporta PointCab: `C:\PointCab\Proyecto_Demo\Imagenes\<vista>\…`),
  PNG y JPG en una carpeta por vista y un DXF de marco y ejes como referencia externa.

### Lo que hace el editor (compilación con «tv-imagenes-locales» y «tv-mosaicos»)

| Qué | Cómo |
| --- | --- |
| Imágenes y referencias del plano | consulta `references` → `images` (handle, path, size, shown, fade, clipped, block, supplied), `xrefs` (name, path, overlay, status), `supplied_bytes` |
| Qué se ve de cada imagen | consulta `image_views` → `views` (path, size, rect en píxeles con y hacia abajo, density, screen_area), `camera_revision`, `viewport_size`, `screen_scale` |
| Qué mosaicos necesita la vista | consulta `image_tiles` → `views` (lo de `image_views` más `set`: el archivo atado y la rejilla, `level`, `file_rect`, `want`: hasta 64 `[nivel, col, fila]` en orden, `complete`), `tiles_bytes`, `budget_bytes` |
| Insertar | op `image_attach` (`path`, `size`, y `world_file` o `geotiff` o `insert` + `width` + `rotation`; `layer`, `send_to_back`) |
| Ajustes de una imagen | op `image_set` (`handle`; `fade`, `transparency`, `shown`, `clip_rect` o `null`, `draw_order`, `detach`) |
| Atar una imagen a un archivo | `wasmBindings.ocs_image_tiles_open(document_id, ruta, ident, anchoArchivo, altoArchivo)` |
| Dar un mosaico | `wasmBindings.ocs_image_tile(document_id, ruta, ident, nivel, col, fila, ancho, alto, rgba)` (el bloque con su margen; `Stale` si la ruta ya está atada a otro archivo) |
| Soltar | `ocs_image_tiles_release(document_id, ruta)`; con ruta vacía, todo lo de ese documento |
| Dar un dibujo referido | `wasmBindings.ocs_xref_supply(ruta, bytes, document_id)` (solo ese documento vuelve a resolver sus xrefs) |
| Desvincular | `ocs_image_tiles_release(document_id, "")` (las demás pestañas conservan lo suyo); `ocs_local_reset()` suelta todo |

Dar píxeles o dibujos no es una edición: no deja paso de deshacer ni marca «Sin guardar».

## Fuentes SHX

Los planos mexicanos usan txt, romans, simplex, isocp y bigfonts. El editor web no las trae; quien administra las sube desde su AutoCAD en
**CAD → Fuentes** (`/cad/fuentes/`, solo `admin`): una o varias `.shx` (se revisa el encabezado «AutoCAD-86 shapes/unifont/bigfont»), hasta
2 MB cada una y 50 MB en total; subir una con el mismo nombre la reemplaza.

- **Dónde:** `TV_HERRAMIENTAS_ARCHIVOS/fuentes/<sha256>.shx` y la tabla `FuenteCad`, fuera de `cad/`: entran en el respaldo normal (son pocos
  kB) y no hay que volver a subirlas tras un cambio de servidor. Nunca se publican: solo las bajan cuentas con CAD.
- **Al abrir un plano:** `editor.js` las baja (dirección con su huella, `Cache-Control: private, immutable` y `ETag`) y las registra con
  `font_register` (compilación tv-v2026.45-5) antes de abrir el plano; el editor las tiene solo en memoria. Una que falla no detiene nada:
  quien administra ve un aviso discreto, y la página «Fuentes» marca «El editor no la pudo usar». El editor informa cuántas letras tiene.

## Imprimir a PDF y convertir: prueba de factibilidad (15-11-2026)

El motor nativo de Linux (`OpenCADStudio --serve`, protocolo 1, `plot` y `save`) se ensayó en la VM Ubuntu 24.04 aarch64, **sin pantalla ni
GPU** y sin variables de entorno de escritorio: sus pruebas de GPU fallan y sigue sin ventana (`"mode": "headless"`). Imprime PDF 1.3 en
Carta, Oficio, Doble carta y A0–A5, en espacio modelo o una página por presentación, a escala o ajustado, y guarda DWG ↔ DXF (con
`lossy_conversion_not_acknowledged` cuando se perdería algo).

| Plano (DXF) | Abrir | Imprimir A1 ajustado | Pico de memoria |
| --- | --- | --- | --- |
| de prueba (5 entidades) | < 0.1 s | < 0.1 s | 94 MB |
| 20 000 entidades mezcladas | 0.14 s | 0.69 s | 0.66 GB |
| 50 000 mezcladas | 0.32 s | 1.8 s | 1.55 GB |
| 100 000 mezcladas | 0.6 s | 4.5 s | 2.5 GB |
| 50 000 líneas | — | 0.15 s | 0.43 GB |
| 50 000 polilíneas | — | 0.58 s | 0.60 GB |
| 50 000 textos | — | 4.0 s | 2.49 GB |

Los textos dominan (~50 kB cada uno). De ahí el trabajador propio `tv-tareas-cad` (cola `cad`, una tarea a la vez, `MemoryMax` 2 GB,
`OOMPolicy=continue`). Por la pila real en la VM: 15 000 textos se imprimen en 2 s; 50 000 (~2.5 GB) fallan en 5 s con «demasiado grande», el
núcleo mata solo al motor (que se marca primero con `oom_score_adj` 1000) y el siguiente trabajo sale bien sin reiniciar el trabajador. Con
`MemoryHigh` el motor solo se frenaba hasta agotar su tiempo (5 min), y sin marcarse primero el núcleo de la VM mataba a los trabajadores de
Python. Un proceso por trabajo: la memoria se libera completa. El AppImage se extrae al desplegar (sin FUSE). El de x86_64 se comprueba al desplegar (`tvctl` lo arranca sin pantalla).

**Lo que hace la página al imprimir** (`cad/nativo.py`):
- **Presentaciones:** con `layout:"all"` el motor imprimiría también el espacio modelo y una hoja en blanco por cada presentación vacía
  (la «Presentación1» que traen casi todos los planos). Por eso se leen las presentaciones (`records` de `objects/Layout`), se descartan
  las que no tienen nada en su bloque de papel salvo la ventana general (Viewport de id 1), se imprime cada una aparte en el orden de sus
  pestañas y se unen con `pypdf` (BSD, Python puro). Sin ninguna con dibujo se dice «Imprime el espacio modelo».
- **Escala:** el motor toma «1:100» como 1 mm de papel = 100 unidades; en espacio modelo se convierte según las unidades del plano
  (`header.insertion_units`; sin unidades, metros). Las presentaciones ya están en mm de papel.
- **Fuentes:** las de CAD → Fuentes se enlazan en la carpeta de fuentes del motor antes de arrancarlo.
- **Memoria:** el motor corre con `RLIMIT_DATA` = `HERRAMIENTAS_CAD_MEMORIA_MB` − 256 MB y su aborto por memoria se dice como «demasiado
  grande»; además, tope de archivo de 2 GB, sin volcados y con sus avisos en un archivo (no en una tubería que nadie lee).

## Pruebas

```sh
uv run pytest itt_herramientas/pruebas/test_cad.py                    # unitarias, con un motor de prueba que habla el mismo protocolo
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_navegador.py -k "not webkit"   # luego -k webkit; un navegador a la vez
uv run pytest itt_herramientas/pruebas/test_cad_datos_moviles.py                         # ETag/304, gzip, latido, panel de Civil, sw.js
uv run pytest itt_herramientas/pruebas/test_cad_pestanas.py                              # pestañas: bloqueo por pestaña, la hoja, permisos
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_pestanas_navegador.py -k chromium   # Esc en cada camino y las pestañas; luego webkit
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_mosaicos_navegador.py      # mosaicos: nítido al volver, nivel anterior al acercar, sin costuras a escala 1–1.5 con la vista girada
TV_CAD_MEDIR=<GeoTIFF con el patrón de nitidez> uv run pytest \
  itt_herramientas/pruebas_navegador/test_cad_mosaicos_navegador.py -k medir            # la medición de «Ortofotos enormes» (cad_local_rasters grande --nitidez)
TV_CAD_TACTIL=1 ITT_DATOS_CAD=/tmp/datos uv run pytest \
  itt_herramientas/pruebas_navegador/test_cad_datos_moviles_navegador.py -k chromium      # «Datos móviles», ~10 min por motor (tiempo real)
```
