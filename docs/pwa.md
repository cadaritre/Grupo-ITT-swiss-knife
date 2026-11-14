# PWA de Herramientas: cómo funciona y qué falta probar en teléfono

Estado: IT-3, 13-11-2026. Diseño: `TresVizo-Portal/docs/arquitectura.md` §18 y `herramientas-web.md`.

## Piezas

| Pieza | Dónde | Qué hace |
| --- | --- | --- |
| Manifiesto | `/manifest.webmanifest` | «TresVizo Herramientas», `scope` y `start_url` = `/`, `display: standalone`, íconos 192/512 y maskable |
| Service worker | `/sw.js` (alcance `/`) | Guarda la interfaz con versión y las páginas `/capturar/` e `/instalar/` (sin datos de cuenta). Sin red, cualquier navegación abre `/capturar/`. No guarda `/subidas/`, `/sesion/` ni páginas con datos. No se activa solo: ofrece «Hay una versión nueva · Actualizar» |
| Cola local | `static/herramientas/offline/cola.js` | IndexedDB. Una captura = una transacción; solo cuenta como guardada cuando la transacción termina |
| Subida | `tus.js`, `subidor.js` | tus 1.0.0 contra `/subidas/` (contrato de `tvplataforma`). Guarda la URL de la subida **antes** de enviar datos, así reanuda tras un cierre. Borra la captura local **solo** cuando el servidor confirma el mismo `sha256` (`X-TV-Sha256` y `GET /subidas/<id>/`) |
| Destino | `herramientas.captura` | Valida que sea JPEG/PNG de hasta 40 MP, aplica cuota por cuenta y guarda con el `sha256` como nombre. Idempotente por (cuenta, sha256): reenviar la misma foto no duplica |
| Reducción | `reducir.js`, `reducir.worker.js`, `exif.js` | Web Worker + `OffscreenCanvas`: lado mayor 2400 px, JPEG 0.85, orientación EXIF aplicada. La hora y el GPS se leen del EXIF **antes** de reducir. Sin Worker, usa `<canvas>` en el hilo principal |
| Interfaz común | `ui.js` | Contador «N pendientes» siempre visible, `beforeunload` con pendientes, `navigator.storage.persist()`, aviso al 80 % del espacio, subida al abrir / al volver la señal / al volver a primer plano / cada 30 s |
| Web Share | `compartir.js` | `navigator.share({files})` desde un gesto; si no se puede, descarga |
| Guía | `/instalar/` | Pasos para iPhone (Safari) y Android (Chrome) y el estado de instalación y de almacenamiento |

El contador y la banda aparecen en todas las páginas de Herramientas y en `/capturar/`. `/capturar/` e `/instalar/` son públicas
(HTML idéntico para todas las cuentas, sin token ni nombre); los datos de la cuenta llegan por `/sesion/`, que exige sesión.

## Pruebas

- Servidor: `pytest itt_herramientas/pruebas/test_pwa.py` (manifiesto, versión del service worker, destino, idempotencia, cuota, permisos).
- Navegador: `pytest itt_herramientas/pruebas_navegador` con Playwright en Chromium (perfil de Pixel 7) y WebKit (perfil de iPhone 13).
  Instalación normal: `pip install -e '.[navegador]'` y `python -m playwright install chromium webkit`.
  Recorre: modo avión, cierre forzado y reapertura, corte a media subida, confirmación por `sha256`, error del servidor,
  rechazo permanente, sesión caducada, aviso al cerrar, almacenamiento, Web Share, reducción en Worker, manifiesto y service
  worker, nueva versión sin activación automática, y diseño a 360 px con objetivos táctiles de 44 px.
  Corre bajo el candado de la máquina compartida.

## Lo que Playwright NO puede probar (hay que probarlo en un iPhone y un Android reales)

WebKit de Playwright es el motor, **no iOS Safari**: no tiene el sistema de almacenamiento, procesos ni permisos de iOS.
Sin teléfono real quedan sin verificar:

1. **Desalojo de almacenamiento.** Safari puede borrar los datos de un sitio sin uso (7 días sin interacción, en pestaña). Lo
   que sí hace la app: pide `persist()`, recomienda instalar y avisa al 80 %. Si iOS concede `persist()`, cuánto espacio da la
   app instalada y si respeta los datos tras semanas sin abrirla **no se puede simular**. Prueba: capturar 40 fotos en modo
   avión, no abrir la app 8 días y comprobar que siguen.
2. **Pestaña de Safari y app instalada tienen almacenamientos separados.** Playwright no tiene «app instalada». Prueba: capturar
   en la pestaña, instalar y confirmar que la app NO ve esas fotos (la interfaz lo avisa: «están en este navegador»).
3. **Sin Background Sync ni subida con la app cerrada en iOS.** Se sube solo con la app abierta. Verificar con el teléfono:
   cerrar la app con pendientes y confirmar que no suben hasta reabrirla. En Android Chrome sí hay Background Sync
   (`itt-subir`); su disparo real con la app cerrada tampoco se prueba en Playwright.
4. **Cierre forzado real en iOS** (deslizar la app fuera del selector, bloqueo de pantalla, llamada entrante a media subida). Aquí
   se cierra el navegador entero, que es lo más cercano, pero iOS puede suspender la app de otra manera.
5. **Cámara nativa y permisos.** `capture="environment"` abre la cámara del sistema; Playwright solo inyecta un archivo. Falta ver:
   la orientación EXIF de fotos reales, el tamaño real de una foto de 12–48 MP en memoria al reducirla (**posible límite de
   memoria en iPhone antiguos con `OffscreenCanvas`**), y que iOS entregue **sin ubicación** en el EXIF (la app usa Geolocation).
6. **Permiso de ubicación en la app instalada**, que iOS puede volver a pedir. Verificar la pregunta, el rechazo («sin ubicación») y
   la precisión en campo.
7. **Web Share con archivos.** Se probó con un `navigator.share` simulado. En iPhone y Android reales hay que comprobar que la hoja
   abre con PDF, que WhatsApp y Correo lo reciben, y que el gesto no se pierde (el archivo se prepara antes del toque).
8. **Inicio de sesión entre hosts** (`tools` ↔ `app`) en la app instalada de iOS: riesgo conocido de arquitectura §18.2. Playwright
   no reproduce que la vista del sistema no comparta la cookie. Es el criterio de salida de IT-3: **probar en un iPhone real**.
9. **Instalación («Agregar a inicio») y el ícono**, el modo `standalone`, la barra de estado y el área segura (`safe-area-inset-bottom`)
   con la banda de pendientes en iPhone con muesca. Los íconos se comprueban por tamaño, no a ojo.
10. **Cuota real de IndexedDB por versión de iOS** (arquitectura: «por confirmar en pruebas»).
11. **Modo avión real** frente a `context.set_offline`: este último corta las peticiones del navegador pero no prueba redes
    «mentirosas» (señal débil que acepta la conexión y no entrega datos). La subida usa reanudación por trozos, pero los plazos
    ante una red así solo se miden en campo.

## Decisiones y deuda conocida (para el informe)

- El contador y el aviso de espacio muestran «dónde están los pendientes» («están en este navegador») cuando no es la app instalada.
- No hay subida «en segundo plano» en iOS: es una limitación de la plataforma, no se simula.
- Las capturas de otra cuenta en el mismo teléfono no se suben con la cuenta actual (se omiten hasta que esa cuenta vuelva a entrar).
- Reportes (IT-7) usará `referencia` para agrupar las fotos de un borrador; aquí solo existe el destino genérico y la página de captura.

## Identidad corporativa (Grupo ITT / TresVizo)

- Todas las cuentas trabajan con **Grupo ITT** salvo las que tienen el rol `herramientas.cambiar_perfil` («Cambiar perfil
  corporativo»), que **solo concede el propietario** desde la consola del portal. Administrar perfiles (`herramientas.admin`) NO
  da derecho a cambiar de perfil.
- Quien no lo tiene no ve selector ni pista alguna; un POST a `/perfil-activo/` responde 403. Quien lo tiene ve la franja de
  marca con el logo, el nombre («Herramientas TresVizo»), el sitio y los dos botones, con el activo marcado. La elección se
  guarda **por cuenta** (`PerfilActivo`) y nunca afecta a otra persona.
- Con TresVizo activo cambian: la franja de marca (logo y sitio), los tokens de color (`/perfil.css`, desde la paleta del perfil),
  el nombre y los íconos del manifiesto que enlazan las páginas con sesión (`/manifest.webmanifest?p=tresvizo`), el
  `apple-touch-icon`, y todo documento nuevo (cotizaciones, reportes, croquis…).
- Límites honestos: el encabezado general del portal («TresVizo») pertenece al armazón y no cambia. El manifiesto y los íconos
  de una app **ya instalada** no se actualizan solos al cambiar de perfil: se fijan al instalar (el navegador los relee según
  su propio calendario). Las páginas públicas (`/capturar/`, `/instalar/`) no llevan identidad porque no se sabe quién las abre.
- Paridad: los 22 casos corren con los dos perfiles activos en el escritorio (`main`) y en la web (44 comparaciones,
  `paridad/`), y `golden_windows.py` genera también el juego de TresVizo.

## Cotizaciones (IT-5)

`/cotizaciones/` es una página fija (igual para todas las cuentas) que el service worker guarda: abre sin conexión. Todo lo demás
llega de IndexedDB y de la API JSON (`/cotizaciones/api/…`, con sesión y rol «Cotizaciones»).

| Pieza | Dónde | Qué hace |
| --- | --- | --- |
| Datos locales | `cotizaciones/borradores.js`, almacén `borradores` de `itt-cola` (versión 2) | Cada cotización vive primero en el teléfono. «Sucia» = el servidor no tiene su última versión; cuenta en «N pendientes» y en el aviso antes de cerrar |
| Sincronización | `ITT.sincronizadores` (`ui.js`) | Con cada disparador de subida: sube los borradores sucios y baja lo nuevo. Idempotente por `id_cliente` |
| Conflicto | `servicio.sincronizar` | Si el servidor cambió desde la revisión en que se basó el teléfono, la versión del teléfono se guarda como **copia** («… (desde el teléfono)»); el original no se toca. Reenviar el mismo conflicto no duplica |
| Totales | `cotizaciones/totales.js` | Misma aritmética que `QuoteData`: `round(x, 2)` con empate al par sobre el valor binario exacto y `sum` compensado de CPython. `pruebas/test_cotizaciones_totales.py` lo compara con Python con ~4000 cotizaciones al azar y los empates clásicos |
| PDF | `POST /cotizaciones/api/pdf/` | El mismo `generate_quote_pdf` del escritorio con el perfil activo de la cuenta: `test_pdf_web_identico_byte_a_byte_al_del_dominio`. Vista previa con PDF.js (`static/herramientas/vendor/pdfjs`, 4.10.38 «legacy», Apache 2.0) porque los teléfonos no muestran PDF en línea |
| Corrector | `POST /cotizaciones/api/ortografia/` | El mismo `BilingualSpellService` (es/en, términos técnicos, diccionario personal de la cuenta); diálogo paso a paso con cambiar / cambiar todas / ignorar / agregar |
| Machotes | `/cotizaciones/api/machotes/…` | Los 14 de fábrica (es/en) y los de la cuenta, con exportar/importar en el esquema del escritorio y versión anterior al sobrescribir |
| Fotos | cola de capturas | Se reducen en un Worker y viajan por la misma cola tus que las capturas (`captura:<sha256>` en la cotización). El PDF avisa de las que aún no llegan |

Lo que necesita conexión (a propósito, y el editor lo dice): generar el PDF y su vista previa, la revisión ortográfica (el
diccionario vive en el servidor), cambiar el idioma (traduce el texto de fábrica en el servidor), guardar/borrar machotes e
importar un editable. Crear, editar, duplicar y borrar cotizaciones, agregar conceptos de cualquier machote ya descargado y tomar
fotos de referencia funciona sin señal.

Límites sin probar en teléfono real (se suman a los de arriba):

- El service worker sincroniza **fotos** en segundo plano (Android), no **borradores**: un borrador se sube cuando la app se abre
  con señal. La página lo muestra como «Sin sincronizar» y el contador lo cuenta.
- Cotización hecha en la pestaña de Safari y en la app instalada: almacenamientos separados (ver punto 2).
- La vista previa con PDF.js y su rendimiento en iPhone antiguos con cotizaciones de 15 conceptos y varias fotos.
- Compartir el PDF con WhatsApp/Correo (punto 7).
