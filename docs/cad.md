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
- Bloqueo con latido (30 s; caduca a los 90), borrador de recuperación cada 2 minutos con cambios, sin el aviso «¿Abandonar el sitio?».
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
  conexión al navegar a `/cad/…` sin señal y sus estilos y scripts desde `itt-cad-pagina`. Nunca guarda planos ni páginas con datos.
- **«Disponible sin conexión» por plano** (base `tv-cad-campo` de IndexedDB, de la cuenta): llevarlo **no** toma el bloqueo. Sin conexión
  se ve, se mide y se dibuja en `/cad/sin-conexion/`; lo cambiado queda como «cambios en el teléfono», nunca una versión. Con conexión:
  versión nueva solo si la del servidor sigue siendo la base y nadie tiene el bloqueo; si no, una copia. El servidor lo exige igual.
- **Cerrar sesión**: las copias de planos se borran; los cambios sin subir no se pierden sin avisar (se ofrece descargarlos; si se sale
  igual, quedan ocultos en el teléfono hasta que vuelva a entrar la misma cuenta). Con la sesión cerrada la página sin conexión no muestra
  nada. Al quitar el CAD del teléfono se borra el editor; un plano con cambios sin subir no se puede quitar.

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
```
