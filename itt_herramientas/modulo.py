from tvplataforma.modulos import Modulo, Rol
from tvplataforma.navegacion import Accion, Busqueda, Entrada

MODULO = Modulo(
    clave="herramientas",
    nombre="Herramientas Grupo ITT",
    host="tools.tresvizo.com",
    urlconf="itt_herramientas.urls",
    icono="herramientas/img/modulo.png",
    orden=20,
    descripcion="Reportes, cotizaciones, croquis, conversión de planos y CAD",
    # El service worker, el manifiesto, la guía de instalación y la página de captura (HTML sin datos de ninguna cuenta,
    # necesaria para arrancar sin conexión) se sirven sin sesión: el navegador los pide sin poder seguir un inicio de sesión.
    limpiezas=("itt_herramientas.convertidor.servicio.limpiar", "itt_herramientas.drive.servicio.limpiar", "itt_herramientas.supervision.servicio.limpiar",
               "itt_herramientas.comun.zips.limpiar", "itt_herramientas.cad.servicio.limpiar", "itt_herramientas.comun.documentos.limpiar"),
    # Al eliminar una cuenta (contrato 1.8) se quita su conexión con Google Drive y se cancela su cola; sus documentos se quedan.
    # CAD: se sueltan los planos que tenía abiertos para editar y se borran sus borradores de recuperación (los planos se quedan).
    # Civil: se borran los formatos de puntos que la cuenta guardó (son suyos).
    # Cotizaciones, reportes y croquis son de la empresa: se quedan con el nombre de su autor; se sueltan los que tenía abiertos y se borran
    # sus borradores de recuperación.
    al_eliminar_cuenta=("itt_herramientas.drive.servicio.al_eliminar_cuenta", "itt_herramientas.cad.servicio.al_eliminar_cuenta",
                        "itt_herramientas.civil.servicio.al_eliminar_cuenta",
                        "itt_herramientas.comun.documentos.al_eliminar_cuenta"),
    rutas_publicas=("sw.js", "manifest.webmanifest", "capturar/", "instalar/", "cotizaciones/", "reportes/", "croquis/"),
    # Navegación como datos (contrato 1.5, §12): el armazón pinta con esto el lateral, el cajón del teléfono y la paleta, por rol.
    # Las páginas de campo (que abren sin conexión) pintan el mismo menú con `{% lateral_de_campo %}`.
    menu=[
        Entrada("Inicio", "house", "herramientas:inicio", clave="inicio", atajo="g h"),
        Entrada("Cotizaciones", "receipt", "herramientas:cotizaciones", roles=("cotizaciones", "admin"), grupo="Campo", atajo="g o", clave="cotizaciones"),
        Entrada("Reportes fotográficos", "camera", "herramientas:reportes", roles=("reportes", "admin"), grupo="Campo", atajo="g r", clave="reportes"),
        Entrada("Croquis de ubicación", "map", "herramientas:croquis", roles=("croquis", "admin"), grupo="Campo", atajo="g m", clave="croquis"),
        Entrada("Capturar fotos", "images", "herramientas:capturar", roles=("reportes", "cotizaciones", "admin"), grupo="Campo", clave="capturar"),
        Entrada("Convertidor", "arrow-right-left", "herramientas:convertidor", roles=("convertidor", "admin"), grupo="Gabinete", atajo="g v", clave="convertidor"),
        Entrada("Triangulación", "triangle", "herramientas:triangulacion", roles=("triangulacion", "admin"), grupo="Gabinete", atajo="g t", clave="triangulacion"),
        Entrada("CAD", "drafting-compass", "herramientas:cad", roles=("cad", "admin", "supervision"), grupo="Gabinete", atajo="g d", clave="cad"),
        # Quien edita ya ve todos los documentos de la empresa en su herramienta: «Supervisión» queda para quien solo supervisa.
        Entrada("Supervisión", "users-round", "herramientas:supervision", roles=("supervision", "admin"), grupo="Supervisión", atajo="g s", clave="supervision"),
        Entrada("Ajustes", "settings", "herramientas:preferencias", grupo="Cuenta", atajo="g a", clave="ajustes"),
    ],
    acciones=[
        Accion("Nueva cotización", "file-plus", "/cotizaciones/#/nueva", roles=("cotizaciones", "admin"), atajo="n"),
        Accion("Machotes de cotización", "library-big", "/cotizaciones/#/machotes", roles=("cotizaciones", "admin")),
        Accion("Nuevo reporte fotográfico", "camera", "/reportes/#/nuevo", roles=("reportes", "admin")),
        Accion("Nuevo croquis", "map-pin-plus", "/croquis/#/nuevo", roles=("croquis", "admin")),
        Accion("Convertir un archivo", "file-up", "herramientas:convertidor", roles=("convertidor", "admin")),
        Accion("Nuevo plano", "file-plus-2", "/cad/#nuevo", roles=("cad", "admin")),
        Accion("Subir un plano", "file-up", "/cad/#subir", roles=("cad", "admin")),
        Accion("Papelera de planos", "trash-2", "herramientas:cad_papelera", roles=("cad", "admin")),
        Accion("Fuentes de CAD", "type", "herramientas:cad_fuentes", roles=("admin",)),
        Accion("Conectar Google Drive", "hard-drive-upload", "herramientas:drive"),
        Accion("Supervisar documentos", "users-round", "herramientas:supervision", roles=("supervision", "admin")),
        Accion("Papelera de documentos", "trash-2", "herramientas:papelera", roles=("cotizaciones", "reportes", "croquis", "admin", "supervision")),
    ],
    busqueda=Busqueda("herramientas:buscar", "Buscar documentos y planos", icono="file-text", roles=("cotizaciones", "reportes", "croquis", "cad", "admin", "supervision")),
    roles=[
        Rol("reportes", "Reportes fotográficos", "Toma fotos en campo y genera el reporte en PDF. Ve y edita todos los reportes de la empresa."),
        Rol("cotizaciones", "Cotizaciones", "Crea cotizaciones, también sin señal, y genera su PDF. Ve y edita todas las cotizaciones y machotes de la empresa."),
        Rol("croquis", "Croquis de ubicación", "Dibuja el área sobre el mapa y genera el croquis en PDF y DXF. Ve y edita todos los croquis de la empresa."),
        Rol("convertidor", "Convertidor DXF ↔ KML/KMZ", "Convierte planos y polígonos entre DXF, KML y KMZ."),
        Rol("triangulacion", "Triangulación, pendientes y escurrimiento", "Genera un TIN, zonifica pendientes y dibuja el escurrimiento."),
        Rol("cad", "CAD", "Abre, crea, sube y edita los planos DWG y DXF de la biblioteca compartida; elimina los suyos."),
        # «Cuadros de construcción a DXF» (IT-10) no se ofrece hasta que exista la herramienta.
        Rol("admin", "Administrador de herramientas", "Perfiles corporativos (identidad, textos, paleta, imágenes) y diccionario técnico. Elimina cualquier documento o machote y "
            "vacía las papeleras. En CAD, además, toma el control de un plano que otra persona edita."),
        Rol("supervision", "Supervisión", "Ve las cotizaciones, los reportes y los planos de la empresa, con su autor, sin editarlos. Solo lectura."),
        Rol("cambiar_perfil", "Cambiar perfil corporativo", "Puede cambiar entre los perfiles Grupo ITT y TresVizo. Solo lo concede el propietario."),
    ],
)

#: Roles de las herramientas (todos menos `admin`): quien tenga alguno puede usar los comunes.
ROLES_DE_HERRAMIENTAS = tuple(r.clave for r in MODULO.roles if r.clave not in ("admin", "cambiar_perfil"))
#: Quien supervisa también entra al inicio, a sus ajustes y a la sesión de las páginas de campo (`supervision` va en la tupla de arriba).

