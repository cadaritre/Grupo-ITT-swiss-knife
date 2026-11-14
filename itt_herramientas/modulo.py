from tvplataforma.modulos import Modulo, Rol

MODULO = Modulo(
    clave="herramientas",
    nombre="Herramientas Grupo ITT",
    host="tools.tresvizo.com",
    urlconf="itt_herramientas.urls",
    icono="herramientas/icono.svg",
    orden=20,
    # El service worker, el manifiesto, la guía de instalación y la página de captura (HTML sin datos de ninguna cuenta,
    # necesaria para arrancar sin conexión) se sirven sin sesión: el navegador los pide sin poder seguir un inicio de sesión.
    limpiezas=("itt_herramientas.convertidor.servicio.limpiar", "itt_herramientas.drive.servicio.limpiar"),
    rutas_publicas=("sw.js", "manifest.webmanifest", "capturar/", "instalar/", "cotizaciones/", "reportes/", "croquis/"),
    roles=[
        Rol("reportes", "Reportes fotográficos"),
        Rol("cotizaciones", "Cotizaciones"),
        Rol("croquis", "Croquis de ubicación"),
        Rol("convertidor", "Convertidor DXF ↔ KML/KMZ"),
        Rol("triangulacion", "Triangulación, pendientes y escurrimiento"),
        Rol("cuadros_dxf", "Cuadros de construcción a DXF"),
        Rol("admin", "Administrador de herramientas", "Perfiles corporativos (identidad, textos, paleta, imágenes) y diccionario técnico."),
        Rol("cambiar_perfil", "Cambiar perfil corporativo", "Puede alternar entre las identidades Grupo ITT y TresVizo. Solo lo concede el propietario."),
    ],
)

#: Roles de las herramientas (todos menos `admin`): quien tenga alguno puede usar los comunes.
ROLES_DE_HERRAMIENTAS = tuple(r.clave for r in MODULO.roles if r.clave not in ("admin", "cambiar_perfil"))
