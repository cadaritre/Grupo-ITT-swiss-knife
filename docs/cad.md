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

**Una sola excepción, de la página del plano (no del editor): la imagen satelital.** Con la clave del proveedor configurada, la página
`/cad/<id>/` de quien puede dibujar lleva en `connect-src` el origen del servicio de teselas y nada más
(`https://ibasemaps-api.arcgis.com`; `cad/satelite.py`, `poner_csp`: la CSP del armazón con `TV_CSP_EXTRA` más ese origen). Las teselas se
piden desde la página con `fetch` (no desde el trabajador, que en producción llega sin CSP propia: así la que manda es esta), en `cors`,
sin cookies (`credentials: "omit"`) y con `referrerPolicy: "origin"`, porque la clave se restringe a nuestro sitio por el Referer y la
página va con `Referrer-Policy: same-origin` (Caddy). `img-src` no cambia: las teselas llegan como bytes al trabajador, nunca como `<img>`.
Sin clave, o para supervisión, o en una versión anterior, la CSP es la de siempre. Lo prueban `test_cad_satelite.py` (la cabecera) y
`test_cad_satelite_navegador.py` (otro origen se bloquea).

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
| `TVCad.on(evento, fn)` / `off` | `listo`, `cargado`, `cambio` (`{sucio}`), `guardado` (`{revision, formato}`), `pestana`, `editable` (`{editable}`: se tomó o se perdió el control del plano activo) |

Una extensión también puede ir **dentro de la cinta del editor**, como pestaña propia con sus comandos (Civil lo hace: docs/civil.md, «En la
cinta del editor»; contrato del editor en el fork, `docs/tresvizo-cinta.md`). Entonces no usa `#cad-extensiones`.

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
| Una hoja de Civil abierta como paleta junto al dibujo, con el foco en ella o en el dibujo | la cierra (`cad/editor/arranque.js` pregunta a la página); el siguiente Esc es del editor |
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
- **Cambiar de pestaña siempre termina en la pestaña elegida**, también con el equipo cargado. La página lee el estado del editor una vez
  por segundo y, si el editor cambió solo de documento (CLOSE, NUEVO o ABRIR dentro de él), lo sigue. Hasta 1.2.21 esa lectura podía
  ganarle a la persona: caía mientras el plano nuevo aún tomaba su bloqueo o bajaba (el editor seguía mostrando el anterior) o llegaba
  después de cambiar de pestaña habiendo sido pedida antes; la página «seguía» al editor de vuelta al plano anterior, el nuevo quedaba
  detrás y la cabecera, `TVCad` y la carpeta del proyecto (`TVCadLocal`) en el otro plano. Ahora una lectura pedida antes de cualquier
  cambio de la página (mostrar una pestaña, abrir, activar o cerrar un documento) se descarta, y mientras la pestaña activa abre su
  plano la página no sigue al editor. Lo prueba `test_cambiar_de_pestana_con_el_editor_lento_termina_en_la_activa` forzando las dos
  carreras (un bloqueo que tarda 3 s; una lectura del estado retenida hasta después del cambio).
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

## Presentaciones desde plantilla (18-11-2026)

Pedido del dueño: «En AutoCAD se le puede dar clic derecho en la pestaña de layout e importar una plantilla, y eso siempre uso yo para mi
marco de planos.» Es el «Desde plantilla…» de AutoCAD (LAYOUT › Plantilla), con su «Guardar como plantilla…» (LAYOUT › Guardar como).

- **Dónde.** Clic derecho en una pestaña de presentación del editor: «Desde plantilla…» y «Guardar como plantilla…»; en la pestaña Model y
  en «+»: «Presentación nueva» y «Desde plantilla…»; también la orden `LAYOUT` → `Plantilla` (o `T`) y Insertar › Presentación ›
  «Layout from Template…» de la barra de menús. Solo en la pestaña que se puede editar (esta página tiene su bloqueo) y en la computadora:
  en solo lectura, en una versión anterior, en el teléfono y en el modo táctil no aparecen (la página manda `layout_templates {menu}`).
- **Elegir la plantilla** (hoja «Desde plantilla»):
  - **Mis plantillas**: las que esta cuenta marcó con la estrella o guardó con «Guardar como plantilla…», la usada más reciente primero.
    Para el marco de siempre son dos toques: el marco y «Insertar» (con una sola presentación ya va marcada). La marca es de la cuenta
    (`PlantillaCad`); el plano sigue siendo de la biblioteca de todos. Un plano en la papelera no sale; al restaurarlo vuelve.
  - **De la biblioteca**: la misma búsqueda, carpetas de proyecto o cliente y páginas que «Abrir otro plano» (`/cad/elegir/`), con la
    estrella para agregar o quitar de «Mis plantillas». «Plantillas» va primero entre las carpetas.
  - **De esta computadora**: un archivo DWT, DWG o DXF elegido o soltado, o un dibujo de la carpeta del proyecto vinculada (la de
    «Referencias»). Se lee en el navegador y se le da al editor: **nunca se sube** (hasta 200 MB, por la memoria del editor). Para tenerlo
    en la biblioteca, se sube en «Planos».
- **Elegir las presentaciones**: el editor lee la plantilla en su trabajador (`layout_list`) y la hoja muestra cada presentación con su
  vista previa (el SVG que hace el editor; la página solo deja pasar `rect` y `path`), su papel («ISO A1 · 841 × 594 mm») y sus ventanas;
  se marcan una o varias («Todas») e «Insertar 2 presentaciones».
- **Qué hace el editor** (`layout_import`, parche 80; contrato en `docs/tresvizo-plantillas.md` del fork), como AutoCAD:
  - trae cada presentación con su configuración de página (papel, área, escala, estilo de trazado, desfase, giro), todo su espacio papel
    (el marco y el rótulo con sus atributos, textos, ventanas con su vista, escala, capas inutilizadas y bloqueo) y lo que usa: bloques
    (también los anidados), capas, tipos de línea, estilos de texto, de cota y de directriz, imágenes y referencias externas (como
    referencias);
  - **nombres**: una presentación que se llama como una del plano entra como «A1 Marco (2)» (la regla de Copiar de AutoCAD); un bloque,
    estilo o capa que el plano ya tiene se queda como está (se usa el del plano, como INSERT, y el aviso lo dice: «Este plano ya tenía
    MARCO: se usó lo suyo»); los bloques anónimos de la plantilla nunca toman los del plano;
  - **un solo paso de deshacer**, el plano queda «Sin guardar» y se muestra la primera presentación insertada; el espacio modelo de la
    plantilla no se trae: las ventanas muestran el espacio modelo **de este plano** con la vista de la plantilla y la persona las ajusta
    después, como en AutoCAD.
- **Guardar como plantilla…**: un nombre (el de la presentación) y «Guardar plantilla». El editor escribe un DWG con esa presentación sola y
  lo que usa, sin espacio modelo (`layout_template_save`, el escritor DWG del editor: LibreDWG y ezdxf lo leen en las pruebas del fork), y
  queda como plano nuevo de la biblioteca en el proyecto «Plantillas» (`POST /cad/plantillas/guardar/`, rol de edición), marcado en «Mis
  plantillas» de quien lo guardó. Su versión dice «Plantilla de presentación».
- **Servidor**: `GET /cad/plantillas/` (la lista de la cuenta), `POST /cad/plantillas/<id>/` (`{"en": true|false}` marca o quita;
  `{"usada": true}` la sube en la lista) y `POST /cad/plantillas/guardar/`; `/cad/elegir/` dice de cada plano si es plantilla de quien
  pregunta y la dirección de su archivo. Tope: 60 plantillas por cuenta. Al eliminar una cuenta se borra su lista (los planos se quedan).
  Migración `0019_cad_plantillas` (tras `0018_formatos_puntos_civil`).
- **Las dos hojas no van en la página del plano** (~1.3 KB comprimidas por apertura, que el teléfono y el modo táctil nunca usan):
  `plantillas.js` las pide en la computadora al estar listo el editor, `GET /cad/plantillas/hojas/?v=<huella>` (iguales para todas las
  cuentas; con la huella vigente se guardan un año, `ETag` y 304), como el panel de Civil. Así la apertura con datos móviles sigue bajo
  30 KB con la cinta, las plantillas y la imagen satelital juntas (los textos fijos del proveedor satelital van en el panel de Civil).
- **Editor**: hace falta la compilación con el parche 80 del fork (rama `tv-plantillas-layout`); con una anterior la hoja no se abre
  porque el editor no ofrece las opciones. Probado con la compilación local `tv-v2026.45-cf` (servida como la versión fijada).
- **Página**: `static/herramientas/cad/plantillas.js` (el gancho `openCadStudioLayoutTemplate` del editor, la hoja y el menú),
  `templates/herramientas/cad/plantilla.html`; `window.TVCadPlantillas` es para las pruebas. Pruebas: `pruebas/test_cad_plantillas.py` y
  `pruebas_navegador/test_cad_plantillas_navegador.py` (Chromium y WebKit: el menú de la pestaña, las tres fuentes, que nada local se
  sube, permisos y bloqueo, pestañas).

## Modo táctil (teléfono y tableta) — detrás de `TV_CAD_TACTIL`

Apagado por omisión (`settings.TV_CAD_TACTIL` o la variable de entorno del mismo nombre: «1»). Apagado, nada cambia: la página del plano es
la de siempre, el service worker no lleva la parte sin conexión (`offline/sw_cad.js`) y `/cad/sin-conexion/` responde 404 (`cad/tactil.py`).
El editor guardado en el equipo no depende de este ajuste (abajo, «El editor guardado en el equipo»).

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
  solo en la app instalada (iOS borra lo de un sitio no instalado a los 7 días sin uso) y con `navigator.storage.persist()`. Lo guarda el
  mismo código que en la computadora (`instalador.js`, abajo «El editor guardado en el equipo»): Cache Storage `itt-cad-editor-<versión>`,
  cada archivo comprobado con el SHA-256 de `MANIFIESTO.json`, y al cambiar la versión fijada solo lo que cambió (copias y parches); la
  anterior se borra. La versión instalada se lee de la base del teléfono: también sin conexión. Las páginas de campo que no usan CAD
  nunca bajan el editor.
- **El service worker**: `offline/sw_cad_editor.js` (siempre) sirve el editor guardado; `offline/sw_cad.js` (agregado por `sw.py` solo con
  el ajuste) abre la página sin conexión al navegar a `/cad/…` sin señal, sus estilos y scripts desde `itt-cad-pagina` y el panel de Civil desde `itt-cad-pagina-civil`.
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
- **La página**: el panel de Civil (~20 KB comprimidas, igual para todos) llega aparte una vez por versión (docs/civil.md); la página sin
  conexión se vuelve a guardar solo cuando cambia su huella (`tactil.pagina`, en la configuración de la página del plano); el editor nuevo
  se baja **antes** de abrir el marco y solo lo que cambió, y la primera descarga usa lo que el navegador ya trajo al abrir el plano
  (Chromium; WebKit no lo reusa).
- **El service worker** (docs/pwa.md): `sw.js` lleva `ETag` (Chromium recibe un 304), se sirve sin sus líneas de comentario y solo importa
  la cola, tus y el subidor donde hay Background Sync (Android): WebKit, que no lo tiene, los bajaba enteros en cada página.

Lo que sigue viajando en cada apertura (lo que debe ir al servidor): la página del plano (sus datos: versión vigente, bloqueo, borrador),
la pregunta de versión (304), tomar y soltar el bloqueo, `perfil.css` (los colores del perfil, red primero) y, si el navegador la busca, la
versión del service worker (304; WebKit pide `sw.js` entero, ~6 KB, y también el manifiesto). Por minuto: un latido.

## El editor guardado en el equipo (18-11-2026)

Pedido del dueño: «que también se pueda descargar OpenCAD en el caché en la PC, para no estar descargue y descargue lo mismo». Antes el
editor (~33 MB con gzip, casi todo el `.wasm`) vivía solo en la caché HTTP del navegador: Chrome la recorta cuando se llena y cada
compilación nueva (`/cad/editor/<versión>/`, a veces varias por semana) bajaba el `.wasm` entero otra vez.

**Qué ve la persona.**
- **Computadora (y tableta):** automático. La primera vez que abre un plano, la carga dice «Guardando el CAD en esta computadora… 12 de
  37 MB» y el editor abre de lo guardado. «Más acciones» termina con «CAD guardado en esta computadora · 118 MB» y, debajo, «Se abre sin
  descargar nada y se actualiza solo con lo que cambie.» (o, si el navegador no concedió el almacenamiento persistente, «… Si el
  navegador se queda sin espacio podría borrarlo; se vuelve a guardar solo.»), con el botón «Quitar». Quitado: «El CAD se descarga cada
  vez que abres un plano.» y «Guardar el CAD en esta computadora». Con una versión nueva: «Actualizando el CAD de esta computadora… 1.2
  de 3.4 MB».
- **Teléfono:** como antes, a pedido («Descargar CAD en este teléfono» en «Sin conexión» del modo táctil, solo en la app instalada), con el
  mismo código; sus actualizaciones también bajan solo los parches.

**Por qué automático en la computadora.** Es lo que el navegador ya bajaba al abrir un plano (la misma cantidad de bytes la primera vez),
ahora guardado de forma fiable y visible, con «Quitar». Un botón «Descargar» sería un paso más para lo mismo, y quien no lo tocara
seguiría bajando el editor en cada versión. En el teléfono sigue a pedido: son datos móviles y espacio de un teléfono, y iOS borra lo de un
sitio no instalado.

**Cómo funciona** (`static/herramientas/cad/instalador.js`, `window.TVCadEditor`; lo usan `editor.js` y `campo.js`):
- Cache Storage `itt-cad-editor-<versión>` (la misma de antes), `navigator.storage.persist()` y la marca de versión completa en la base
  `tv-cad-campo` (`estado`, `editor:<versión>`), escrita al final. `/cad/paquete-del-editor/` (`cad/guardado.py`, con o sin modo táctil)
  da la lista: ruta, SHA-256 de `MANIFIESTO.json`, tamaño, lo que se baja y los parches de cada archivo.
- Se guarda **antes** de abrir el marco y el marco abre de lo guardado (el service worker, `offline/sw_cad_editor.js`, siempre presente):
  la primera vez el editor viaja una sola vez; después, cero bytes del editor al reabrir y en cada pestaña nueva. Si el service worker no
  está, no hay Cache Storage o algo falla, el editor se abre de la red como siempre.
- Una sola instalación a la vez en todo el navegador (`navigator.locks`): dos pestañas que abren planos a la vez no bajan dos veces.
- **Versión nueva, solo lo que cambió:** lo de igual SHA-256 (en cualquier ruta) se copia de la versión guardada; el `.wasm` y el `.js`
  principales (cambian de nombre en cada compilación) se arman con un parche en `static/herramientas/cad/parche.worker.js`; lo demás se
  baja entero. La versión anterior se borra cuando la nueva está completa.
- **Seguridad:** cada archivo se lee de vuelta de la caché y se compara con el SHA-256 del manifiesto antes de darlo por guardado; el
  parche se arma, se compara con el SHA-256 y solo entonces se guarda. Cada archivo lleva su tamaño y su SHA-256 en cabeceras
  (`X-TV-Bytes`, `X-TV-SHA256`): el service worker no sirve uno que no mida lo que debe y borra esa versión entera (la próxima apertura la
  rehace). Si el editor no arranca desde lo guardado, la página borra esa versión y lo abre de la red. Una versión a medias no tiene su
  marca y no cuenta como guardada (sus archivos ya comprobados se aprovechan al terminarla).
- **Página y editor, siempre la pareja exacta:** la página pide `/cad/editor/<versión fijada>/…` y el service worker solo responde con esa
  versión; nunca un editor de otra versión.
- **Cerrar sesión no borra el editor** (`sesion.js` no toca Cache Storage): es el programa, igual para todas las cuentas, sin datos de nadie.
  Se borra con «Quitar» (o «Quitar el CAD de este teléfono»), al borrar los datos del sitio o si el navegador lo desaloja.
- **CSP sin cambios:** el trabajador es un script de `/static/` (`worker-src 'self'`) y solo usa `fetch`, Cache Storage y `crypto.subtle`;
  no lleva WebAssembly.

**Los parches** (`cad/editor/preparar.py`): `upstream.json` lista en «anteriores» las 2 o 3 compilaciones previas (hoy -11 y -10). Al
preparar, para cada una y cada archivo del paquete oficial que cambió (≥ 16 KB), `bsdiff` hace el parche, que se pasa al formato TVP
(los registros y el «extra» al principio, la diferencia al final: se aplica leyendo de corrido, con solo el archivo viejo, el nuevo y unos MB
en memoria), se comprueba aplicándolo y comparando el SHA-256 y se publica solo si pesa menos de la mitad que el archivo entero. Quedan en
`<versión>/_parches/<versión anterior>/<ruta>.tvp.{br,gz}`, con sus SHA-256 en `MANIFIESTO.json` («parches»); en disco solo comprimidos:
Caddy entrega el `.br` (o el `.gz`) con su `Content-Encoding` y el navegador lo descomprime. Se guardan en la caché de preparar
(`~/.cache/tresvizo-cad/parches/`, por los SHA-256 de los dos archivos): bsdiff tarda ~1 min con el `.wasm` y no se repite.

**Medición -11 → -12** (la pareja real; el `.wasm` de 98.6 → 98.7 MB, el `.js` de 185 → 189 KB; lo demás igual):

Medido el 18-11-2026 en esta Mac. «Proxy»: `pruebas_navegador/test_cad_guardado_navegador.py`, perfil de escritorio que se guarda
(1280 × 860), todo por el contador de bytes de «Datos móviles» (HTTP/1.1 con cabeceras; MB = 1 048 576 bytes); lo del editor es lo que pasó
por `/cad/editor/`. «Caddy»: Caddy 2.11 local con las reglas de `tools.caddy`, bytes del cuerpo según su registro.

| Escenario | Chromium | WebKit |
| --- | ---: | ---: |
| Primera vez: abrir un plano guarda el editor (proxy) | 37.6 MB | 37.6 MB |
| Reabrir el plano (proxy) | 0 B del editor | 0 B del editor |
| Pestaña nueva con el plano (proxy) | 0 B del editor | 0 B del editor |
| Uso normal, cualquier apertura después (proxy) | 0 B del editor | 0 B del editor |
| -11 → -12 **sin** parches (antes: el `.wasm` y el `.js` enteros) | 31.4 MB | 31.4 MB |
| -11 → -12 con el parche TVP (B), por el proxy (Chromium recibe brotli en `localhost`; WebKit, gzip) | 3.35 MB | 4.07 MB |
| -11 → -12 con el parche TVP (B), por Caddy con HTTPS (brotli en los dos) | 3.35 MB | 3.35 MB |
| -11 → -12 con `dcz` (A), por Caddy (Chromium usa el `.wasm` de -11 como diccionario; WebKit no conoce `dcz`) | 4.59 MB | 31.4 MB |
| Parche dañado: ese archivo se baja entero y el resultado queda con su SHA-256 (proxy) | 31.5 MB | 31.5 MB |
| Lo guardado dañado: el editor abre de la red (Chromium, de su caché HTTP) y se vuelve a guardar | 5.3 MB + 0 | 68.3 + 36.8 MB |

Detalle de -11 → -12: el `.wasm` es 32 894 635 B con gzip; su parche, 3 510 529 B con brotli (4 257 880 con gzip); con `dcz`
(`zstd -22 --long=27 --patch-from`) 4 812 319 B. El `.js`: 27 444 B con gzip, parche 1 141 B (brotli), `dcz` 1 045 B. Lo demás que cambia
es nuestra página (`index.html`, `release.json`: ~2 KB). Desde -10 el parche del `.wasm` es de 3 440 913 B. Armar el parche en el
navegador: ~0.4 s para el `.wasm` (con su SHA-256). Preparar los parches de dos versiones anteriores: 4 minutos y 1.9 GB de memoria
(bsdiff), una sola vez (caché). Lo guardado en el equipo: 117.5 MB (sin las letras chinas, japonesas y coreanas, que se bajan solas si un
plano las usa). `dcz` solo funcionó con un origen de confianza sin errores de certificado: con el certificado de prueba ignorado
(HTTPS local), Chromium no anuncia `dcb`/`dcz`.

«Datos móviles» con este cambio (`test_cad_datos_moviles_navegador.py`, Chromium / WebKit): (e) primera apertura con el editor nuevo
41.3 / 42.8 KB (ahora incluye volver a guardar la página sin conexión, que nombra la versión del editor) y reabrir 26.4 / 28.4 KB; (f)
reabrir 26.5 / 28.6 KB. Todo dentro del objetivo.

**La opción elegida: el parche TVP (B), no Compression Dictionary Transport (A).**
- **Pesa menos:** el `.wasm` -11 → -12 son 3.35 MB con el parche y brotli contra 4.59 MB con `dcz` (en Chromium; en WebKit `dcz` no existe y baja 31.4 MB).
- **Funciona en todos:** Chromium, Edge, Safari/WebKit, Firefox y el teléfono, con el mismo código; `dcz` solo existe en Chrome y Edge.
- **No depende de la caché del navegador:** el diccionario de `dcz` es el `.wasm` viejo tal como lo guardó el navegador en su almacén de
  diccionarios cuando lo bajó de la red. Con el editor guardado en Cache Storage el `.wasm` ya no se pide a la red, y ese almacén se recorta
  como la caché HTTP: justo cuando hace falta puede no estar. El parche se aplica sobre lo que tenemos guardado y comprobado.
- **Caddy no sabe `dcz`:** habría que generar reglas por versión con el hash de cada diccionario (`Available-Dictionary`) y el
  `Use-As-Dictionary` de cada archivo; el parche son dos reglas fijas (`.br`/`.gz`).
- `dcz` (A) no se implementa. La medición de A se hizo con Caddy local y Chromium reales (`Use-As-Dictionary` en el `.wasm` de -11, `dcz`
  precalculado con `zstd --patch-from --long=27 -22`).

**Lo que hay que hacer al desplegar** (además de lo de siempre):
1. En la Mac, para preparar la versión: `brew install bsdiff brotli` (una vez; `preparar-release.sh` se niega sin ellos). La primera vez
   baja también los ZIP de -11 y -10 (con `gh`, por SHA-256) y tarda ~3 minutos más por los parches; después salen de la caché.
2. En el servidor, **antes o junto con** `tvctl desplegar`, el `tools.caddy` nuevo: `sudo sh ~/despliegue/caddy/sitio.sh habilitar tools`
   (recarga Caddy). Sin él CAD funciona igual y solo las actualizaciones se bajan enteras; tv-salud avisa («Caddy no entrega los parches del
   editor de CAD»).
3. `sudo sh ~/despliegue/salud/instalar.sh` para el aviso nuevo `cad:parches` de tv-salud.
4. `tvctl desplegar` ya comprueba los parches (cada `.br` y `.gz` con su SHA-256 del manifiesto y el `.gz` descomprimido contra el parche
   probado en la Mac).
5. Nada para el dueño en la consola. Al actualizar el editor fijado, pasar la versión anterior al principio de «anteriores» en
   `upstream.json` (y dejar 2 o 3).

**A comprobar a mano en Windows (Chrome y Edge):** abrir un plano → la carga dice «Guardando el CAD en esta computadora…»; «Más acciones» →
«CAD guardado en esta computadora · 118 MB»; cerrar y abrir otro plano → abre en segundos y en DevTools › Red, filtrando `cad/editor`, todo
sale «(ServiceWorker)»; en DevTools › Aplicación › Almacenamiento, «Persistente» (si Chrome no lo concedió, la nota del menú lo dice);
cerrar sesión y volver a entrar → sigue guardado; «Quitar» → ya no está y el siguiente plano lo baja de la red; «Guardar el CAD en esta
computadora» → vuelve. Con la siguiente versión del editor desplegada: la carga dice «Actualizando el CAD de esta computadora… x de 3.4 MB» y en Red solo aparece el `.tvp` del `.wasm` (~3.4 MB), no el `.wasm`.

## Planos grandes (18-11-2026)

Pedido del dueño: «La prueba de fuego es el plano que está en la plataforma, de aprox. 45 MB, y no lo puede abrir OpenCAD». Un plano maestro
de un cliente: DWG de AutoCAD 2018 de 48.7 MB, ~518 000 entidades en ~1 300 bloques (nueve de cada diez, líneas), ~780 000 primitivas con
los bloques expandidos. No se abría ni en Chrome de Windows ni en el iPhone.

**La causa** (medida, con el editor de producción tv-v2026.45-12 y aparte en nativo): no era la lectura del DWG (0.44 s, completa, sin
errores) ni la página. El editor dibuja el espacio modelo desde un solo juego de líneas con todos los bloques expandidos, sin importar la
vista; para este plano armarlo pedía ~5 GB (las letras como cuadros de 336 bytes por letra, cada punto dos veces, un índice con una entrada
por segmento) y el editor web tiene 4 GB: la memoria del editor llegaba a 3.9 GB en 6–9 s y se detenía (`unreachable`). En Chromium el
`open` nunca respondía; en WebKit respondía y el editor moría al dibujar. La página esperaba sin decir nada.

**El arreglo, en el editor** (parches 90–92 del fork, `TRESVIZO.md`):
- El índice de lo que se toca y se engancha (selección, referencias a objetos) guarda tramos de hasta 32 segmentos o puntos y los resuelve
  junto al cursor: ~1/30 de su tamaño, los mismos resultados.
- Pasando de 150 000 primitivas expandidas, el espacio modelo se arma **para la vista**: una zona de tres vistas de ancho alrededor de lo
  que se ve, con nivel de detalle (lo que mide menos de 2 píxeles no se arma, el texto de menos de 3 píxeles de alto tampoco, los puntos de
  las curvas a medio píxel) y un tope de 384 MB por zona: si no cabe, se vuelve a armar con esos umbrales al doble. Mover la
  vista dentro de la zona no arma nada; al salir de ella o acercar más del doble, se arma la siguiente. **La vista completa de un plano así
  muestra menos detalle que de cerca, como un mapa**; al acercar aparece todo. La extensión (ZOOM Extensión, el encuadre al abrir) sale de
  las entidades, nunca de armar el plano entero. Los planos normales se dibujan exactamente como antes.
- «Abriendo el plano… N %»: la descarga (hasta 35 %), lo que el editor lleva leído (`open_progress`, 35–95 %) y la primera vista.

**Medido** (Mac, Chromium con agente de Windows y la GPU, y WebKit; la memoria es la del editor, WebAssembly):

| Plano | Antes · Chromium | Antes · WebKit | Con 90–92 · Chromium | Con 90–92 · WebKit |
| --- | --- | --- | --- | --- |
| Real, 48.7 MB DWG: abrir | no abre (3.9 GB a los 8.5 s, se detiene) | abre en 6.2 s y muere al dibujar | abre en 15 s; al armar la primera vista la memoria llega a ~3.8 GB y se detiene | abre en 9.9 s (3.76 GB), acercar/alejar, guardar el DWG en 1.6 s |
| Sintético, 25.5 MB DWG (`plano_grande.py`): abrir | — | — | 8.8 s, 1.15 GB | 6.8 s, 1.15 GB |
| Sintético: mover la vista (mediana / p95 por cuadro) | — | — | 31 / 77 ms | 17 / 61 ms |
| Sintético: editar (una línea), guardar DWG, reabrir | — | — | 0.09 s, 1.4 s, 18 s | 0.07 s, 1.2 s, 14 s |

**Lo que faltaba (19-11-2026, parches 94–97 del fork).** Con los parches 90–92 el plano real todavía detenía a Chromium al armar sus
vistas: cada vista pedía de golpe ~2.3 GB de trabajo, fuera del tope de 384 MB. Se midió dónde (parche 94: un contador de memoria por
fase, solo en compilaciones de medición, nunca en la de producción), y eran dos cosas:
- **Los tipos de línea con texto** (un lindero de kilómetros con un texto en su patrón, dentro de un bloque) se recorrían enteros, un trazo por guion, aunque
  la vista mostrara un pedazo. Ahora se recorren para la vista: solo los guiones y letras de la zona, en el mismo lugar que si se
  recorriera todo; si a ese zoom el patrón mide menos de 4 píxeles, la línea se ve continua, como se vería de todos modos.
- **El detalle de las curvas al acercar.** Cuando la vista se queda quieta el editor afina las curvas que se ven; a una referencia de
  bloque la volvía a armar entera, y el bloque maestro de este plano son ~780 000 primitivas: ~2 GB de golpe en cada vista. Ahora en un
  plano así se afinan solo las curvas del bloque que están en pantalla (curvas de nivel, splines, polilíneas punteadas con arcos,
  sombreados con bordes curvos; los arcos y círculos sólidos ya se dibujan exactos), dentro del mismo tope. Una referencia chica (hasta
  20 000 primitivas) se sigue armando entera, como antes.

**Medido después** (misma Mac, Chromium con agente de Windows y la GPU, y WebKit; la memoria del editor solo crece, así que la del final
es el pico de toda la corrida: abrir, mover, recorrer el plano en 16 paradas a cuatro escalas, editar, guardar y reabrir):

| Plano | Chromium | WebKit |
| --- | --- | --- |
| Real, 48.7 MB DWG: abrir y primera vista | 13.7 s, 1 450 MB | 9.4 s, 1 392 MB |
| Real: mover la vista (mediana / p95 por cuadro) | 16.5 / 23.8 ms | 20 / 32 ms |
| Real: cada parada del recorrido (mediana / la más lenta) | 0.36 / 1.38 s | 0.31 / 1.02 s |
| Real: editar, guardar DWG (43.6 MB), reabrir | 0.44 s, 3.6 s, 11.2 s | 0.13 s, 4.5 s, 9.2 s |
| Real: pico de toda la corrida | **2 070 MB** | 2 063 MB |
| Sintético, 25.3 MB DWG, con la compilación anterior | abre y se detiene a 4 094 MB | — |
| Sintético, después: abrir, pico de toda la corrida | 6.5 s, 2 148 MB | 5.4 s, 2 135 MB |

El sintético (`plano_grande.py`) lleva ahora los linderos con tipo de línea de texto: con la compilación anterior repite la falla en
Chromium y con la nueva pasa en los dos motores; es la prueba permanente (`plano_grande_web.py`). Ya no hace falta abrir ese plano en
Safari. Se evaluó la memoria de 64 bits de WebAssembly (más de 4 GB, solo Chrome y Edge): no hace falta (el plano queda en ~2.1 GB de
4 GB) y la cadena de compilación actual no la produce (objetivo de Rust de nivel 3, wasm-bindgen sin soporte, Safari no la tiene).

**Teléfono.** Un teléfono deja a una pestaña mucha menos memoria que una computadora y, si el editor pasa de ella, el navegador cierra la
página sin aviso. La página estima lo que el editor necesitará **antes de bajar el editor y el plano** (`editor.js`, `MEMORIA_EDITOR`): 300
MB y 40 MB por MB de DWG o 10 por MB de DXF (medido: el editor solo ~270 MB, un DWG denso de 25.5 MB 1 153 MB, un DXF de 22 MB 371 MB; se
toma un poco más). Con pantalla táctil (el dedo como puntero principal), si pasa de 1 000 MB (en Android, 1 000 MB por cada 4 GB que diga
`navigator.deviceMemory`, que la especificación tapa en 8: a lo más 2 000 MB) aparece «Este plano es demasiado grande para este teléfono.» con «Pesa N MB y abrirlo necesita más
memoria de la que el navegador de este equipo deja usar: se cerraría sin avisar. Ábrelo en una computadora o descárgalo.», «Descargar el
plano» e «Intentar abrirlo aquí». Con ese tope caben en el iPhone los DWG de hasta ~17 MB y los DXF de hasta ~70 MB; el plano de 48.7 MB, no
(necesitaría ~2.2 GB). **El tope del teléfono no se pudo medir aquí** (sin teléfonos ni simuladores, regla de la máquina): 1 000 MB es lo
conservador para un iPhone o iPad de 4 GB, donde iOS deja a una pestaña algo más de 1.5 GB y el navegador, la página y la GPU toman lo suyo.
Si en un equipo concreto sobra, «Intentar abrirlo aquí» lo intenta.

**Borradores de recuperación de un plano grande.** Un borrador es el plano entero: el editor lo reescribe (segundos y memoria en uno grande)
y se sube todo (con gzip un DWG baja ~14 %; el real, de 48.7 a 42.0 MB). Cada 2 minutos serían ~1.3 GB por hora de cambios. **Decisión:** el
intervalo crece con el tamaño: 2 minutos hasta 10 MB, luego 2 minutos por cada 10 MB, a lo más 15 (el real: cada ~10 minutos, ~4 MB por
minuto de cambios como mucho); al pasar la app a segundo plano sale si el anterior ya tiene la mitad del intervalo. Lo que se puede perder
en el peor caso son esos minutos. Se pesó guardarlos solo en este navegador (IndexedDB): no protege si se cambia de equipo, el costo de
reescribir el plano sigue igual y obliga a otra copia del plano de un cliente en el equipo; un borrador por partes no sirve, porque un DWG
cambia por secciones comprimidas y el editor lo reescribe entero.

Pruebas: `test_cad.py::test_un_plano_grande_se_anuncia_antes_de_abrirlo_en_un_telefono` y
`test_cad_navegador.py::test_un_plano_grande_se_anuncia_en_el_telefono_antes_de_bajar_el_editor` (Chromium y WebKit: el aviso antes de bajar
el editor, «Intentar abrirlo aquí» y, en la computadora, el avance en %); en el fork, `tresvizo/pruebas/plano_grande_web.py` (abrir, mover,
editar, guardar y reabrir el sintético con topes de tiempo y memoria) y `bounded::tests`, `piece_tests`.

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
  - **El aviso se oculta** con su × («Ocultar el aviso»; pedido del dueño, 19-11, en el iPhone): se recuerda en este dispositivo por cuenta
    y plano, con las rutas que faltaban (`localStorage tv.cad.aviso-local`, hasta 200 planos; sin almacenamiento, solo en esa visita).
    Vuelve solo si falta **otra** ruta (p. ej. una versión nueva con otra imagen). «Referencias» y los marcos siguen igual: la carpeta se
    vincula después desde ahí.
  - **Donde aquí no se vinculan carpetas** (el modo táctil; un equipo solo táctil sin `showDirectoryPicker`, como Safari de iOS, que dice
    tener `<input webkitdirectory>` pero no deja elegir carpetas) el aviso es una línea compacta, sin «Vincular»: «La imagen de este plano
    está en una computadora; aquí ves su marco.» (o «Las imágenes…, sus marcos»), con la ×. Se decide por lo que el navegador puede
    (`showDirectoryPicker`, o `webkitdirectory` con un ratón o trackpad: `any-pointer: fine`), no por el agente de usuario. En un
    teléfono angosto ocupa a lo más dos líneas. Lo prueban `test_el_aviso_de_la_carpeta_se_oculta_y_vuelve_si_falta_otra` y
    `test_en_el_telefono_el_aviso_es_una_linea_sin_vincular_y_se_cierra` (Chromium y WebKit).
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

## Imagen satelital (Civil → Imágenes, 18-11-2026)

Dónde: el grupo «Imágenes» de la cinta «Civil» del editor (botón grande «Imagen satelital», comando `CVSATELITE`; `MENUS` de
`civil/pagina.py`, clave `satelite`, grupo `imagenes`, junto a «Imagen georreferenciada» y «Referencias»), y «Más acciones» →
«Imagen satelital…» del plano. `satelite.js` registra la acción con `TVCivil.registrarAccion("satelite", …)`, también si Civil llega
después (evento `tvcivil:listo`). Con un editor sin pestañas propias, el grupo va en la barra de respaldo de Civil. Para quien puede
dibujar, en la versión vigente.

Como «Importar imagen de Google Earth» de CivilCAD: la persona elige el área (una polilínea cerrada o dos esquinas) y la calidad, y la imagen
queda en el plano como un IMAGE georreferenciado, guardada en su computadora. Código: `static/herramientas/cad/local/satelite.js` (la hoja y
el trabajo), `satelite-trabajador.js` (mosaico y reproyección), `satelite-geo.js` (la geometría), `cad/satelite.py` (proveedor, clave y CSP),
la hoja `civil-hoja-satelite` de `civil_panel.html` y el gancho `insertarArchivo` de `local.js`.

### Proveedor y términos (revisados el 18-11-2026)

| Proveedor | ¿Deja guardar la imagen en un archivo del proyecto? | Dónde lo dice |
| --- | --- | --- |
| **Google** (Maps Platform: Map Tiles, Static Maps; Google Earth y Earth Pro) | **No.** | Términos de Google Maps Platform §3.2.3(a) (no exportar, extraer, guardar ni bajar teselas en lote fuera de los servicios), §3.2.3(b) (no guardar en caché salvo lo que permitan los términos de cada servicio; los de Map Tiles API no lo permiten) y §3.2.3(c) (no crear contenido a partir de él, p. ej. calcar del satélite). Términos de servicio específicos §8.2 (Google Earth: no bajar ni exportar). Para Earth Pro, las «Geo Guidelines» de Google (sin numeración): exportar imágenes solo para usos no comerciales, y no usar su salida para crear otro contenido |
| **Mapbox** Satellite (Static Images, teselas) | **No.** | Mapbox Product Terms (20-10-2026) §1.9(v) (no exportar, bajar, guardar en caché ni almacenar), §2.8.1 (caché de hasta 30 días en el mismo dispositivo, sin distribuirla como imagen estática), §1.7 y §1.7.3 (las copias impresas o digitales estáticas solo con derechos de impresión, que se limitan a Mapbox Streets) y §1.6 (calcar el satélite solo para uso no comercial u OpenStreetMap) |
| **Esri** World Imagery por **ArcGIS Location Platform** | **Sí, como imagen estática final.** | ArcGIS Location Platform Agreement (E204, 21-11-2025) §3.1(d)(6)(B): el resultado puede usarse en formato estático (PDF, GIF, JPEG, HTML) en forma final inalterable; §3.1(d)(4): atribución pegada al resultado del servicio de mapas base; §3.1(d)(1) y (d)(2): no redistribuir y una petición sirve a una sola persona; §3.1(a)(3): no quitar avisos; §3.2: términos de los proveedores de datos |
| Esri World Imagery con una cuenta de ArcGIS Online | Solo fuera de línea en aplicaciones de ArcGIS («World Imagery (for Export)») | Descripción de la capa y resumen de términos de Esri: no descargar ni alojar contenido de Esri fuera de sus paquetes |
| INEGI (ortofotos) | Su licencia de libre uso lo permitiría, pero su servicio WMS no se puede usar desde el navegador (cortafuegos que rechaza las peticiones, sin CORS) y sus ortofotos son más viejas y gruesas | — |
| Bing Maps | Las cuentas gratuitas se retiraron (pasó a Azure Maps, con suscripción de Azure) | — |

**Decisión:** Esri World Imagery por ArcGIS Location Platform, en su nivel gratuito (2 000 000 teselas de mapa base al mes; después,
US$0.15 por cada 1 000). En México tiene 30 cm (Vantor «Vivid Advanced», más de mil zonas urbanas) a 60 cm–1.2 m (Vivid Standard) y, en
algunas ciudades, 15 cm. **Cómo cumplimos el §3.1(d):** se guarda una sola imagen estática por petición de la persona (JPEG en forma final,
no las teselas: estas se descartan al armar el mosaico y nunca se guardan), se usa para ver el plano, cada persona baja la suya, nada se
redistribuye desde nuestro servidor (nunca la ve), y la atribución va en la imagen, en un archivo junto a ella y, si se recorta, en el
plano. Topes por imagen (abajo) para que sea una imagen de un proyecto y no una descarga masiva.

**Respaldo:** el servicio es configurable (`HERRAMIENTAS_CAD_SATELITE_SERVICIO`): cualquier MapServer de teselas de ArcGIS cuyos términos lo
permitan (p. ej. el de un gobierno estatal o un ArcGIS Enterprise propio) funciona igual, con su origen en la CSP. Y siempre queda la
ortofoto propia (dron) con «Insertar imagen georreferenciada».

**Clave:** una clave de API de ArcGIS Location Platform con el privilegio de mapas base, restringida por Referer a
`https://tools.tresvizo.com`. Es pública en el navegador por diseño (así la usa Esri); la restricción de Referer y el nivel gratuito la
acotan. Va como ajuste del servidor `HERRAMIENTAS_CAD_SATELITE_CLAVE` (en el Portal, desde los secretos, como la de Drive), **nunca en el
repositorio**. Sin ella la hoja dice «La imagen satelital aún no está activada en este servidor».

### Lo que hace

- **Área:** la polilínea cerrada (sus arcos en cuerdas) o dos esquinas, más un margen (10 m por omisión). Se pasa de la zona UTM del plano
  (la de «Escala y textos» de Civil) a geográficas con las series de Krüger (GRS80, error < 0.1 mm).
- **Calidad:** «Máxima disponible» (el nivel más fino con imagen en al menos el 95 % del área, según el mapa de teselas del proveedor), 15
  y 30 cm, 60 cm, 1.2 y 2.4 m por píxel, u otra en cm. Antes de bajar nada dice el tamaño en píxeles, el tamaño en el terreno, las teselas y
  su nivel, lo que se baja y lo que pesa el archivo, y el tiempo. Si el proveedor no llega a esa calidad ahí, lo dice; donde falta una
  tesela del nivel, usa la de un nivel más bajo (hasta 4).
- **Topes:** 40 millones de píxeles (≈ 6 300 × 6 300) y 12 000 por lado: una imagen mayor no cabe en la memoria del navegador al armarla
  y codificarla; y 2 500 teselas por imagen. La hoja dice qué calidad sí cabe o que se divida el área.
- **Bajar:** del navegador al proveedor, 8 a la vez, con reintentos; «Detener» (o cerrar la hoja) corta todo y no deja nada.
- **Armar:** en un trabajador, por bandas de 256 filas (memoria acotada a unas filas de teselas): cada píxel de salida, en la cuadrícula
  UTM, toma su lugar exacto en Mercator (malla de nodos exactos cada 32 px, interpolada: error < 0.005 px) con interpolación bilineal. Así
  la imagen queda en la cuadrícula del plano, sin giro, y cae sobre los puntos levantados. JPEG de calidad 0.9 con la atribución abajo a
  la derecha.
- **Guardar (nunca en el servidor):** con la carpeta del proyecto vinculada (Chrome y Edge), se pide **una vez** el permiso de escritura y
  se guardan en `Satelital\` el JPEG, su archivo de mundo `.jgw` (centro del píxel superior izquierdo, convención de ESRI), el `.prj` de la
  zona y un `.txt` con la atribución y los datos (zona, esquinas, píxel, nivel, fecha); un nombre que ya existe toma « (2)». Sin carpeta,
  o en Safari y Firefox, se descargan el JPEG, el `.jgw` y el `.txt`, y la imagen se inserta para la sesión (la hoja ofrece vincular la
  carpeta).
- **Insertar:** por el camino de «Insertar imagen georreferenciada» (`TVCadLocal.insertarArchivo`, `image_attach` con el archivo de
  mundo): IMAGE estándar con su IMAGEDEF y la ruta relativa (`Satelital\nombre.jpg`), en la capa «Imagen satelital», debajo del dibujo
  si se pide. **Recorte a la polilínea:** polígono del IMAGE (`set_properties` de `clip_boundary`, la ruta del editor ya lo dibuja) y la
  atribución como MTEXT bajo el área; todo en un `batch` con `bindings`: **un solo paso de deshacer**. Si el editor no aceptara el
  polígono, el lote se deshace entero y se recorta al rectángulo de la polilínea (`image_set` con `clip_rect`), y se dice.

### Pruebas

- `pruebas/test_cad_satelite.py`: UTM ↔ geográficas contra pyproj en las zonas 11 a 16 y al sur (< 0.1 mm), Web Mercator contra EPSG:3857,
  el nivel por calidad, el plan (esquinas en múltiplos del píxel, teselas que cubren las esquinas, topes), la malla (< 0.005 px), el
  archivo de mundo y el `.prj` (pyproj lo lee como EPSG:32613 / 32719); la clave solo a quien dibuja y la CSP con solo el proveedor.
- `pruebas_navegador/test_cad_satelite_navegador.py`, con el editor real y un proveedor falso en otro origen que pinta marcas rojas en la
  latitud y longitud de puntos UTM conocidos: estimación contra resultado, cada marca en su píxel del JPEG (centroide a menos de medio
  píxel) y en su lugar en la pantalla del editor, la de fuera del polígono recortada, el IMAGE con su esquina y vectores, la carpeta
  (Chromium) o la descarga (WebKit), un paso de deshacer, nada de las teselas ni de la imagen en las peticiones a nuestro servidor, otro
  origen bloqueado por la CSP, los topes y «Detener».

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
uv run pytest itt_herramientas/pruebas/test_cad_guardado.py                              # paquete, parches (bsdiff + brotli), cómo se sirven
TV_CAD_EDITOR_PRUEBAS=DIR ITT_DATOS_CAD=/tmp/datos uv run pytest \
  itt_herramientas/pruebas_navegador/test_cad_guardado_navegador.py -k chromium              # editor guardado y -11 → -12; luego webkit
TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor \
  uv run pytest itt_herramientas/pruebas_navegador/test_cad_mosaicos_navegador.py      # mosaicos: nítido al volver, nivel anterior al acercar, sin costuras a escala 1–1.5 con la vista girada
TV_CAD_MEDIR=<GeoTIFF con el patrón de nitidez> uv run pytest \
  itt_herramientas/pruebas_navegador/test_cad_mosaicos_navegador.py -k medir            # la medición de «Ortofotos enormes» (cad_local_rasters grande --nitidez)
TV_CAD_TACTIL=1 ITT_DATOS_CAD=/tmp/datos uv run pytest \
  itt_herramientas/pruebas_navegador/test_cad_datos_moviles_navegador.py -k chromium      # «Datos móviles», ~10 min por motor (tiempo real)
```
