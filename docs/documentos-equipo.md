# Documentos compartidos con el equipo (cotizaciones, reportes, croquis)

Decisión del propietario (16-11-2026): **«Compartidas con el equipo»**. Las cotizaciones, los reportes fotográficos y los croquis son de la
empresa, no de cada cuenta. El código común vive en `itt_herramientas/comun/documentos.py` (bloqueo, historial, recuperación, papelera,
avisos), `comun/sincronizacion.py` (fusión y copia en conflicto) y, en el navegador, `static/herramientas/offline/compartido.js`.

## Quién ve y edita qué

| Rol | Cotizaciones | Reportes | Croquis | Machotes |
|---|---|---|---|---|
| `cotizaciones` | ve y edita todas | — | — | ve, crea y edita todos |
| `reportes` | — | ve y edita todos | — | — |
| `croquis` | — | — | ve y edita todos | — |
| `admin` | todo, y elimina cualquiera | todo | todo | elimina cualquiera |
| `supervision` | **Supervisión**: solo lectura de cotizaciones y reportes de la empresa (nunca lo de la papelera), y su papelera sin poder recuperar | | | |

- **Eliminar** un documento o un machote: quien lo creó o quien administra (igual que los planos de CAD). Lo eliminado va a la **papelera**
  (`/documentos/papelera/`, 30 días): lo recupera quien lo creó, quien lo eliminó o quien administra; «Eliminar para siempre» es solo de
  quien administra. Pasados 30 días `tv_limpiar` lo borra con su historial.
- Cada renglón y cada encabezado dicen el **autor** y **«Modificada por X · fecha»** (quien guardó la revisión vigente).
- **Navegación**: quien edita ya ve todo en su herramienta; la entrada «Todas las cuentas» pasa a llamarse **«Supervisión»** y la ven el
  rol `supervision`, quien administra y el propietario. Ahí están también los **folios repetidos** de antes de compartir.
- El diccionario personal del corrector sigue siendo de cada cuenta. Las fotos son de la cuenta que las subió; un documento del equipo
  muestra y usa en su PDF las fotos que referencia **si las subió una cuenta autorizada en ese documento** (`autorizados`: quien lo hizo
  o lo editó alguna vez, más lo heredado de su origen cuando es un duplicado o una copia en conflicto; solo crece, recortar el historial
  no lo toca; `capturas.ruta_autorizada`). Poner el sha256 de una foto ajena en un documento propio no la descubre. Las miniaturas se
  piden con `?doc=<id>`, y el PDF del editor solo usa las imágenes guardadas en esa cotización (o las propias).
- Los ZIP de cada herramienta llevan solo sus documentos (un lote de cotizaciones con un reporte responde 422) y nunca lo de la papelera.
- **Sincronización incremental.** Cada guardado que cambia lo que se ve en una lista recibe un número de cambio (secuencia de PostgreSQL;
  un candado de transacción hace que el orden de los números sea el orden en que se confirman). El teléfono pide `?desde=<cursor>` y
  recibe solo lo que cambió, en tandas de 200 (`mas`), con los que se fueron a la papelera en `eliminados`; si desde ese cursor se borró
  algo para siempre, también `vigentes`. Sin cambios, una sincronización es **una sola petición**. El latido del bloqueo no mueve el cursor.
- **Qué se guarda en cada teléfono.** Los documentos que la persona **creó o abrió** en él, con sus datos completos para trabajar sin
  conexión, y se mantienen al día. De los demás del equipo solo el resumen que pinta la lista; sus datos se piden al abrirlos (sin señal,
  la pantalla pide abrirlo una vez con señal). Un teléfono nuevo no baja los datos de toda la empresa. Lo que otra persona manda a la
  papelera se quita del teléfono si no tiene cambios; con cambios se queda y al subir el servidor lo guarda como copia en conflicto. El
  detalle de algo de la papelera responde **410 «eliminado»** (404 si ya no existe).
- **Google Drive**: va a la Drive de la persona que genera el PDF (como antes: «la Drive de quien guarda»).
- **Cuenta eliminada o suspendida**: sus documentos se quedan como registros de la empresa con su nombre de autor; se sueltan los que tenía
  abiertos y se borran sus borradores de recuperación (`documentos.al_eliminar_cuenta`). Una cuenta suspendida deja de latir y su bloqueo
  caduca solo.

## Un editor a la vez

- Al abrir un documento que ya está en el servidor, la página toma el **bloqueo** y late cada **15 s** y al volver a la pestaña; sin latido
  **150 s**, caduca (un navegador deja latir una pestaña en segundo plano solo una vez por minuto).
- Otra persona lo abre en **solo lectura**: «La está editando X desde las hh:mm» y **«Tomar el control»** (con confirmación). Cuando la otra
  persona sale, la página pasa sola a editable.
- A quien pierde el control se le dice en su siguiente latido (y queda un aviso en la lista). Lo que no se había subido llega al servidor con
  la sesión de su página y se guarda como **borrador de recuperación** suyo (`BorradorRecuperacion`, uno por pestaña: dos pestañas que
  pierden el control guardan cada una lo suyo), nunca encima del documento. Lo que se siga escribiendo mientras viaja la petición también
  llega, y también lo que se escriba después en esa misma página (sigue con su sesión). Al abrir el documento ve «Tienes cambios sin guardar…» con **Ver**, **Usar mis cambios** (revisión nueva; lo anterior queda en el
  historial) y **Descartar**. Los borradores de recuperación viven 30 días.
- La misma cuenta en otra ventana: «La tienes abierta en otra ventana» y «Editar aquí».

## Sin conexión (PWA): nunca se pierde nada

Cada guardado lleva la `revision` en que se basó. Al sincronizar:

1. Igual a lo del servidor → nada.
2. Otra persona lo está editando ahora → **copia en conflicto**.
3. El servidor sigue en esa revisión → revisión nueva.
4. El servidor cambió → **fusión de tres vías** con la revisión base guardada en el historial: campo por campo y, en un reporte, **foto por
   foto** (por `sha256`), así lo que se capturó en campo entra aunque en la oficina se haya cambiado otra cosa. Si los cambios se tocan (o la
   base ya no está), queda como **«Copia en conflicto de <folio/título> (X, fecha)»**; el original no se toca y se avisa a quien hizo la versión
   vigente. La copia de una cotización recibe su propio folio. Si cada lado era válido pero la fusión no (p. ej. más fotos que el tope),
   también queda copia: nunca se atora la cola. Si la copia ya existe y llega algo más nuevo: si su versión vigente es la que escribió el
   teléfono, entra como revisión nueva; si alguien la cambió después, se fusiona con eso, y si choca o alguien la está editando, va a otra
   copia. Una copia que está en la papelera no se reutiliza.
   Tras una fusión, si en el teléfono se siguió escribiendo durante la petición, el teléfono pone lo nuevo encima de la fusión con la misma
   regla del servidor (`offline/fusion.js`, con pruebas de que da lo mismo que `fusionar`) y sigue desde la revisión fusionada; solo si eso
   choca conserva la base anterior para que el servidor decida. Nada se pisa sin fusión y seguir escribiendo no crea copias falsas.
5. Se eliminó mientras se editaba → también copia en conflicto.

Las fotos de la cola del teléfono **nunca se tiran**: ni al eliminar un reporte (se suben igual; un reporte de la papelera se recupera con todas),
ni en una copia en conflicto (la copia lleva todas las fotos).

## Historial de versiones

- Se guarda **cada revisión** (`VersionDocumento`): quién, cuándo y, si aplica, por qué («Restaurada de la versión 3», «Fusionada con
  cambios hechos sin conexión», «Copia en conflicto», «Folio nuevo: antes …»).
- Los guardados seguidos de **una misma persona dentro de 10 minutos** se muestran como **una sola versión** (la última del grupo).
- **Retención**: las **100 versiones más recientes** de cada documento; los pasos intermedios de un grupo se guardan **30 días** (son la base
  de la fusión de lo editado sin conexión) y luego los borra `tv_limpiar`.
- Cada versión se ve en solo lectura; en las cotizaciones, también su **PDF tal como era**. **«Restaurar esta versión»** crea una revisión
  nueva (hace falta tener el control). Los PDF siguen a la revisión: el de un reporte deja de estar «al día» y se vuelve a generar.

## Folios de cotización

- **COT-AAAA-NNNN**, asignado por el servidor con el contador del año bloqueado (`ContadorFolio`, `select_for_update`): dos cotizaciones creadas
  a la vez nunca reciben el mismo. Es **uno para toda la empresa** (los dos perfiles, Grupo ITT y TresVizo, comparten la numeración) y la base de
  datos no admite repetidos (`herramientas_cotizacion_folio_unico`).
- El campo «Folio» del editor es de solo lectura. Una cotización nueva sin conexión dice **«Folio al sincronizar»**; el PDF final se genera
  después de sincronizar (la vista previa sin folio dice «BORRADOR»).
- Un editable importado conserva su folio si está libre (se revisa con el contador de su año bloqueado); si ya existe, o otra cotización
  lo toma en el mismo instante, recibe el siguiente y se dice cuál traía.
- **Migración** (`0016_documentos_equipo`): ningún folio cambia solo. Si dos cotizaciones tenían el mismo (antes se contaba por cuenta), la más
  antigua lo conserva y las demás quedan marcadas. En **Supervisión → Folios repetidos** el propietario (o quien administra) pulsa
  **«Asignar folio nuevo»** (también quien administra las herramientas). El contador de cada año sigue desde el mayor folio existente.

## Machotes

Son de la empresa: los ve y edita todo el rol `cotizaciones`, con autor y «modificado por». El nombre es único sin distinguir mayúsculas. En la
migración, si dos cuentas tenían uno con el mismo nombre, el más antiguo lo conserva y los demás pasan a «<nombre> (de <autor>)».

## Registro

Como CAD, en el registro `itt_herramientas.documentos` (con números de cuenta, nunca nombres): tomar el control, restaurar una versión, mandar a
la papelera, recuperar, eliminar para siempre, eliminar un machote y asignar un folio nuevo.

