# Envío a Google Drive (IT-6)

Qué hace, cómo se prueba y **qué pasos son del propietario** (Google Cloud). Nadie más crea credenciales: el código se probó contra un Google
falso (`pruebas/google_falso.py`) y nunca habló con Google.

## Qué hace

Cada cuenta puede conectar **su** Google Drive (página `/drive/`). Lo que genera se copia solo, con la misma estructura y los mismos tipos de archivo que el escritorio:

```
GRUPO ITT REPORTES / <persona> / Cotizaciones          (.pdf y el editable .json de cada cotización final)
                                 Reportes fotograficos  (.pdf)
                                 Poligonos KML          (.kml y .kmz del convertidor)
```

Los DXF, los croquis y las triangulaciones **no** se envían. Sin Drive conectado todo queda en el servidor y se descarga desde cada herramienta.

- **Permiso `drive.file`** (más `openid email` solo para mostrar con qué cuenta se conectó). La app ve únicamente lo que ella crea o lo que la persona le abre con el selector
  de Google (Picker); nunca el resto del Drive.
- **Carpeta compartida con Picker**: se elige una vez la carpeta «GRUPO ITT REPORTES» (la que se comparte con las demás personas); dentro la app crea la carpeta de la persona y
  las tres de categoría. Alternativa sin Picker: «Crear GRUPO ITT REPORTES en mi Drive» (la crea la app, así que la ve).
- **OAuth 2.0 de servidor con PKCE** (`state` en la sesión, verificador de un solo uso, `access_type=offline`, `prompt=consent`). El **token de actualización se guarda cifrado**
  (Fernet con una llave derivada de `SECRET_KEY`; si esa llave cambia, cada persona vuelve a conectar). El navegador solo recibe un token de acceso de corta vida, y solo para abrir Picker.
- **Cola con reintentos**: cada archivo se copia a `drive_cola/` y sale cuando Google confirma el **mismo contenido** (md5). Falla pasajera (sin red, 429, 5xx, corte a media subida)
  → se reprograma con espera exponencial (1 min, 2, 4… hasta 6 h; hasta 12 intentos) y la subida es **reanudable por trozos de 8 MiB**. Si el acceso se revocó, la conexión queda «por
  reconectar» y lo pendiente espera; si la carpeta se borró, pide elegirla de nuevo y lo pendiente espera. Nada se pierde.
- **Nombres que no pisan nada** (como el escritorio): mismo contenido y nombre = no se sube otra vez; otro contenido con el mismo nombre = se agrega una marca de fecha.
  Regenerar sin cambios un PDF que solo cambió de fecha interna **no** se vuelve a mandar (clave por los datos de origen).
- Se envía solo lo **final**: el PDF de una cotización al generarlo para compartir (no la vista previa), el PDF de un reporte al terminar la tarea, el KML/KMZ al convertir.
  La persona puede apagar el envío automático en `/drive/`.

## Qué hace el propietario (Google Cloud, costo 0)

Los nombres de los menús de Google cambian de vez en cuando; lo que importa son los valores.

1. Entra a <https://console.cloud.google.com> con la cuenta de Google del propietario y **crea un proyecto** (p. ej. «TresVizo Herramientas»).
2. **APIs y servicios → Biblioteca**: habilita **Google Drive API** y **Google Picker API**.
3. **Pantalla de consentimiento de OAuth** (en el menú nuevo: «Google Auth Platform»):
   - Tipo de usuario: **Externo**.
   - Nombre de la app, correo de asistencia y correo de contacto del desarrollador.
   - Dominio de la página principal: `https://tools.tresvizo.com`; dominios autorizados: `tresvizo.com`. Google pide también enlaces a la política de privacidad y a las
     condiciones (la página pública de TresVizo, si ya existe).
   - **Permisos (scopes): agrega SOLO `.../auth/drive.file`** (y los básicos `openid` y `.../auth/userinfo.email`). Ningún otro permiso de Drive.
   - Estado de publicación: mientras esté en **«Pruebas»** solo entran las cuentas que agregues como **usuarios de prueba** (hasta 100: las 3 personas), **pero Google vence los
     tokens de actualización a los 7 días** y habría que volver a conectar cada semana. Para uso normal, **publica la app («En producción»)**. `drive.file` no es un permiso sensible, por lo
     que normalmente no pide la verificación completa; si Google la pide, es un trámite del propietario (**por confirmar en la consola**).
4. **Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **Aplicación web**.
   - **Orígenes de JavaScript autorizados**: `https://tools.tresvizo.com`
   - **URI de redireccionamiento autorizados** (exacto, con la diagonal final): `https://tools.tresvizo.com/drive/oauth/callback/`
   - Guarda el **ID de cliente** y el **secreto**.
5. **Credenciales → Crear credenciales → Clave de API** (para Picker):
   - Restricción de aplicación: **Sitios web (referentes HTTP)** → `https://tools.tresvizo.com/*`
   - Restricción de API: **solo Google Picker API**.
6. Anota el **número del proyecto** (Panel → «Información del proyecto»): es el «App ID» de Picker.
7. En Google Drive crea la carpeta **«GRUPO ITT REPORTES»** y **compártela con las tres personas como Editor**. Cada una conecta Drive en `/drive/` y la elige una vez con el selector.
8. Entrega los cuatro valores por el canal seguro de siempre (nunca en el repositorio ni en el chat): ID de cliente, secreto, clave de API y número de proyecto.

## Qué hace quien despliega (Portal)

| Ajuste | Valor |
| --- | --- |
| `TV_DRIVE_CLIENT_ID`, `TV_DRIVE_CLIENT_SECRET` | ya están en el contrato (vacíos = sin Drive) |
| `HERRAMIENTAS_DRIVE_API_KEY` | la clave de API de Picker (nuevo; no está en el contrato todavía) |
| `HERRAMIENTAS_DRIVE_APP_ID` | el número del proyecto (nuevo) |
| `TV_CSP_EXTRA` | lo que necesita Picker (abajo). Sin esto el botón del selector no puede cargar el script de Google y la persona usa «Crear en mi Drive» |
| temporizador | cada 5 minutos: `manage.py herramientas_drive_reintentar` (abajo). Sin él, los reintentos salen al abrir `/drive/`, al enviar otro archivo y en la limpieza diaria |

```python
TV_CSP_EXTRA = {
    "script-src": ["https://apis.google.com", "https://www.gstatic.com"],
    "frame-src": ["https://docs.google.com", "https://content.googleapis.com", "https://drive.google.com"],
    "connect-src": ["https://apis.google.com", "https://www.googleapis.com"],
    "img-src": ["https://*.googleusercontent.com", "https://*.gstatic.com"],
}
```

Esa lista se armó con la documentación de Picker y **no se pudo probar contra Google**: con el selector real puede faltar un host (la consola del navegador lo dice como
«Refused to …»). Es lo primero que hay que probar con las credenciales reales. `TV_CSP_EXTRA` es global del Portal: abre esos hosts para todos los módulos.

Temporizador de systemd (ajusta las rutas):

```ini
# /etc/systemd/system/herramientas-drive.service
[Service]
Type=oneshot
User=tresvizo
WorkingDirectory=/srv/tresvizo/portal
ExecStart=/srv/tresvizo/venv/bin/python manage.py herramientas_drive_reintentar

# /etc/systemd/system/herramientas-drive.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
```

## Qué está probado y qué no

- Probado con el Google falso (25 pruebas de servidor, 6 de navegador en Chromium y WebKit): PKCE y `state`, token cifrado, jerarquía, `drive.file` (la app solo ve lo creado o
  concedido), carga reanudable con cortes, caducidad del token de acceso, límites de uso, revocación, carpeta borrada, nombres repetidos, no duplicar, aislamiento entre cuentas,
  desconexión (revoca y descarta lo pendiente), integración con cotizaciones, reportes y convertidor, y la página con un Picker falso.
- **No probado** (necesita las credenciales del propietario): el consentimiento y el selector reales de Google, la lista exacta de `TV_CSP_EXTRA`, la cuota de Drive de las cuentas,
  el vencimiento real de tokens, y si Google pide verificación al publicar. El cliente sigue la documentación de OAuth 2.0 / Drive v3, pero eso se confirma con la primera conexión real.
