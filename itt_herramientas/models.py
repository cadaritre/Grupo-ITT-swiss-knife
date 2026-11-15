"""Datos comunes de Herramientas: perfiles corporativos y perfil activo, preferencias, diccionarios y machotes.

Las personas se guardan por `cuenta_id` (entero, sin clave foránea): las cuentas son del armazón.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

CLAVES_PERFIL = (("grupo_itt", "Grupo ITT"), ("tresvizo", "TresVizo"))


class ConAutor(models.Model):
    """Quién hizo el documento: foto de la cuenta que lo creó (nombre y correo), tomada en el servidor al crearlo.

    Nunca la escribe la persona ni la cambia una sincronización: se llena una sola vez, cuando el renglón se guarda por
    primera vez, con la cuenta de `cuenta_id`. Queda aunque la cuenta cambie de nombre o se elimine.
    """

    autor_nombre = models.CharField(max_length=150, blank=True, default="", editable=False)
    autor_correo = models.CharField(max_length=254, blank=True, default="", editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self._state.adding and not (self.autor_nombre or self.autor_correo):
            from .comun import autoria

            self.autor_nombre, self.autor_correo = autoria.de_cuenta(self.cuenta_id)
        super().save(*args, **kwargs)


class Perfil(models.Model):
    """Perfil corporativo (herramientas-web.md §3). Hay uno por clave del dominio: `grupo_itt` y `tresvizo`.

    Lo editan los administradores de herramientas; cada cuenta usa los que el propietario le permite. Los datos
    personales (firmante, cédula, firma, datos bancarios) viven solo aquí, en la base, nunca en el código.
    """

    clave = models.CharField(max_length=20, unique=True, choices=CLAVES_PERFIL)
    nombre = models.CharField(max_length=80)
    encabezado = models.CharField("Encabezado de documentos", max_length=120, blank=True)
    encabezado_en = models.CharField("Encabezado (inglés)", max_length=120, blank=True)
    descripcion = models.CharField(max_length=240, blank=True)
    descripcion_en = models.CharField("Descripción (inglés)", max_length=240, blank=True)
    sitio_web = models.CharField(max_length=200, blank=True)
    telefono = models.CharField(max_length=60, blank=True)
    etiqueta_telefono = models.CharField(max_length=20, default="Tel.")
    etiqueta_telefono_en = models.CharField(max_length=20, default="Phone")
    direccion = models.CharField(max_length=240, blank=True)
    correo = models.CharField(max_length=120, blank=True)
    firmante = models.CharField("Firmante de cotizaciones", max_length=120, blank=True)
    pie = models.CharField("Pie de documentos", max_length=160, blank=True)
    pie_en = models.CharField("Pie (inglés)", max_length=160, blank=True)
    # Reportes fotográficos: firmante y cédula profesional de la última página.
    firmante_reporte = models.CharField(max_length=120, blank=True)
    cedula_profesional = models.CharField(max_length=40, blank=True)
    # Datos bancarios de las cotizaciones.
    banco = models.CharField(max_length=60, blank=True)
    clabe = models.CharField(max_length=30, blank=True)
    cuenta = models.CharField(max_length=30, blank=True)
    # Imágenes: rutas relativas a TV_HERRAMIENTAS_ARCHIVOS. La firma y la cédula nunca se ofrecen como descarga.
    logo_ruta = models.CharField(max_length=300, blank=True)
    firma_ruta = models.CharField(max_length=300, blank=True)
    cedula_ruta = models.CharField(max_length=300, blank=True)
    # Textos editables y paleta, con el esquema de `settings.json → documents` del escritorio.
    textos_reporte = models.JSONField(default=dict, blank=True)
    textos_cotizacion_es = models.JSONField(default=dict, blank=True)
    textos_cotizacion_en = models.JSONField(default=dict, blank=True)
    paleta = models.JSONField(default=dict, blank=True)
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["clave"]

    def __str__(self):
        return self.nombre


class PerfilActivo(models.Model):
    """Perfil corporativo que una cuenta con el permiso «Cambiar perfil corporativo» dejó activo.

    Sin renglón, o sin el permiso, la cuenta trabaja siempre con Grupo ITT (como el escritorio por omisión). Solo afecta a la
    propia cuenta: sus documentos y su pantalla, nunca los de otras personas.
    """

    cuenta_id = models.BigIntegerField(unique=True)
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="+")
    actualizado = models.DateTimeField(default=timezone.now)


class Conversion(models.Model):
    """Un archivo del convertidor DXF ↔ KML/KMZ: su origen, lo que se leyó de él y la última salida generada."""

    class Estado(models.TextChoices):
        SUBIDO = "subido", "Subido"
        ANALIZANDO = "analizando", "Analizando"
        LISTO = "listo", "Listo"
        ERROR = "error", "Con error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    nombre = models.CharField(max_length=150)                       # nombre original del archivo
    formato = models.CharField(max_length=3)                        # dxf | kml | kmz
    bytes = models.BigIntegerField()
    ruta_origen = models.CharField(max_length=300)                  # relativa a TV_HERRAMIENTAS_ARCHIVOS
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.SUBIDO)
    zona = models.PositiveSmallIntegerField(default=13)
    hemisferio = models.CharField(max_length=1, default="N")
    resumen = models.JSONField(default=dict, blank=True)             # capas, geometrías, límites, avisos, diagnóstico
    error = models.TextField(blank=True)
    vista_ruta = models.CharField(max_length=300, blank=True)        # GeoJSON de la vista previa
    opciones = models.JSONField(default=dict, blank=True)            # la última conversión pedida
    salida_ruta = models.CharField(max_length=300, blank=True)
    salida_formato = models.CharField(max_length=3, blank=True)
    tarea_id = models.UUIDField(null=True, blank=True)
    creada = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-creada"]


class Preferencias(models.Model):
    """Preferencias recordadas de una cuenta (zona UTM, mapa base, opciones de reportes, croquis, ...)."""

    cuenta_id = models.BigIntegerField(unique=True)
    datos = models.JSONField(default=dict)
    actualizado = models.DateTimeField(default=timezone.now)


class DiccionarioPersonal(models.Model):
    """Palabras que la cuenta agregó al corrector, por idioma: `{"es": [...], "en": [...]}`."""

    cuenta_id = models.BigIntegerField(unique=True)
    palabras = models.JSONField(default=dict)
    actualizado = models.DateTimeField(default=timezone.now)


class TerminoTecnico(models.Model):
    """Diccionario técnico global (lo mantiene `herramientas.admin`); se suma al incluido en el corrector."""

    IDIOMAS = (("es", "Español"), ("en", "Inglés"), ("ambos", "Ambos"))
    palabra = models.CharField(max_length=60)
    idioma = models.CharField(max_length=5, choices=IDIOMAS, default="ambos")
    creado_por = models.BigIntegerField(null=True, blank=True)
    creado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["palabra", "idioma"], name="herramientas_termino_unico")]
        ordering = ["palabra"]


class MachoteCuenta(models.Model):
    """Machote de concepto de cotización de una cuenta (esquema `quotation_templates` del escritorio)."""

    cuenta_id = models.BigIntegerField(db_index=True)
    plantilla_id = models.UUIDField(default=uuid.uuid4)
    nombre = models.CharField(max_length=160)
    idioma = models.CharField(max_length=2, default="es")
    concepto = models.JSONField()
    anterior = models.JSONField(null=True, blank=True)               # versión previa del concepto al sobrescribir el machote
    creado = models.DateTimeField(default=timezone.now)
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cuenta_id", "plantilla_id"], name="herramientas_machote_id"),
            models.UniqueConstraint(fields=["cuenta_id", "nombre"], name="herramientas_machote_nombre"),
        ]
        ordering = ["nombre"]


class Captura(models.Model):
    """Foto de campo recibida de la cola sin conexión del teléfono (reportes y cotizaciones la usarán).

    El `sha256` calculado en el teléfono es la identidad: reenviar la misma foto (por ejemplo, si se perdió la respuesta
    del servidor) devuelve la misma fila y nunca duplica.
    """

    cuenta_id = models.BigIntegerField(db_index=True)
    sha256 = models.CharField(max_length=64)
    ruta = models.CharField(max_length=300)
    bytes = models.BigIntegerField()
    ancho = models.PositiveIntegerField(default=0)
    alto = models.PositiveIntegerField(default=0)
    nombre = models.CharField(max_length=150, blank=True)
    referencia = models.CharField(max_length=100, blank=True, db_index=True)   # p. ej. el borrador de reporte al que pertenece
    orden = models.IntegerField(default=0)
    tomada = models.CharField(max_length=32, blank=True)                       # hora de la captura (EXIF o reloj del teléfono)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    precision = models.FloatField(null=True, blank=True)
    sin_ubicacion = models.BooleanField(default=False)
    nota = models.CharField(max_length=1000, blank=True)
    origen = models.CharField(max_length=10, blank=True)                       # camara | galeria
    recibida = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "sha256"], name="herramientas_captura_sha")]
        ordering = ["referencia", "orden", "id"]


class Cotizacion(ConAutor):
    """Una cotización de la cuenta. `datos` es el JSON de `QuoteData` del escritorio (mismo esquema, compatible en los dos sentidos).

    `revision` sube con cada guardado. El teléfono guarda borradores sin conexión con la revisión en que se basaron: si al
    sincronizar el servidor ya cambió, la versión del teléfono no pisa nada y se guarda como copia.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    id_cliente = models.UUIDField(null=True, blank=True)             # identidad del borrador en el teléfono: hace idempotente la sincronización
    datos = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)
    perfil = models.CharField(max_length=20, default="grupo_itt")
    folio = models.CharField(max_length=60, blank=True)
    cliente = models.CharField(max_length=160, blank=True)
    proyecto = models.CharField(max_length=200, blank=True)
    moneda = models.CharField(max_length=3, default="MXN")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    copia_de = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copias")
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "id_cliente"], name="herramientas_cotizacion_cliente")]
        ordering = ["-actualizada"]


class Reporte(ConAutor):
    """Reporte fotográfico de una cuenta (herramientas-web.md §4.2). Se arma en el teléfono y se sincroniza como borrador.

    `datos` = {titulo, cliente, fecha, con_croquis, con_credenciales, fotos: [{sha256, nota, tomada, lat, lon, precision, sin_ubicacion,
    nombre}]}: el orden de `fotos` es el orden del PDF. Las fotos viven en `Captura` (por sha256); aquí solo se referencian.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    id_cliente = models.UUIDField(null=True, blank=True)
    datos = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    perfil = models.CharField(max_length=20, default="grupo_itt")
    titulo = models.CharField(max_length=200, blank=True)
    cliente = models.CharField(max_length=160, blank=True, db_index=True)      # copia de `datos["cliente"]` para buscar y filtrar
    fotos = models.PositiveIntegerField(default=0)
    copia_de = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copias")
    tarea_id = models.UUIDField(null=True, blank=True)
    salida_ruta = models.CharField(max_length=300, blank=True)               # último PDF generado, relativo a TV_HERRAMIENTAS_ARCHIVOS
    salida_nombre = models.CharField(max_length=200, blank=True)
    salida_revision = models.PositiveIntegerField(null=True, blank=True)     # revisión del reporte con que se generó
    generado = models.DateTimeField(null=True, blank=True)
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "id_cliente"], name="herramientas_reporte_cliente")]
        ordering = ["-actualizada"]


class Croquis(ConAutor):
    """Croquis de ubicación de una cuenta (herramientas-web.md §4.3), con autoguardado en el servidor.

    `datos` es el `SketchData` del escritorio SIN `features` (los vectores pesan megas y los produce el servidor): van en un
    archivo (`vectores_ruta`) que vale solo mientras `vectores_fuente` sea la huella del área, la capa y las curvas actuales.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    id_cliente = models.UUIDField(null=True, blank=True)
    datos = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    perfil = models.CharField(max_length=20, default="grupo_itt")
    titulo = models.CharField(max_length=200, blank=True)
    copia_de = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copias")
    vectores_ruta = models.CharField(max_length=300, blank=True)
    vectores_fuente = models.CharField(max_length=64, blank=True)
    vectorizado = models.DateTimeField(null=True, blank=True)
    vectores_resumen = models.JSONField(default=dict, blank=True)         # {total, conteos, area_km2}
    tarea_id = models.UUIDField(null=True, blank=True)                    # vectorización o exportación en curso
    salida_ruta = models.CharField(max_length=300, blank=True)
    salida_nombre = models.CharField(max_length=200, blank=True)
    salida_formato = models.CharField(max_length=3, blank=True)
    salida_fuente = models.CharField(max_length=64, blank=True)           # huella de los datos con que se generó
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "id_cliente"], name="herramientas_croquis_cliente")]
        ordering = ["-actualizada"]


class Triangulacion(models.Model):
    """Un DXF de triangulación (herramientas-web.md §4.6): su superficie, el análisis de pendientes y el de escurrimiento.

    La superficie (puntos y triángulos) y los análisis viven en archivos `.npz`; aquí quedan solo los resúmenes y las
    opciones con que se calcularon. Cambiar la superficie borra los análisis (dejarían de corresponder).
    """

    class Estado(models.TextChoices):
        SUBIDO = "subido", "Subido"
        ANALIZANDO = "analizando", "Leyendo"
        LISTO = "listo", "Listo"
        ERROR = "error", "Con error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    nombre = models.CharField(max_length=150)
    bytes = models.BigIntegerField()
    ruta_origen = models.CharField(max_length=300)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.SUBIDO)
    error = models.TextField(blank=True)
    resumen = models.JSONField(default=dict, blank=True)            # entidades, capas con triángulos, límites del origen
    superficie = models.JSONField(default=dict, blank=True)         # {origen, opciones, puntos, triangulos, z, limites, estadisticas}
    pendientes = models.JSONField(default=dict, blank=True)         # {opciones, rangos[], area_total}
    escurrimiento = models.JSONField(default=dict, blank=True)      # {opciones, rangos[], flechas, longitud_base}
    tarea_id = models.UUIDField(null=True, blank=True)
    salida_ruta = models.CharField(max_length=300, blank=True)
    salida_tipo = models.CharField(max_length=16, blank=True)       # tin | pendientes | escurrimiento
    salida_nombre = models.CharField(max_length=200, blank=True)
    creada = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-creada"]


class ConexionDrive(models.Model):
    """Conexión de una cuenta con su Google Drive (OAuth `drive.file`, herramientas-web.md §3).

    El token de actualización se guarda CIFRADO (`drive/cifrado.py`). La jerarquía es la del escritorio:
    `GRUPO ITT REPORTES/<persona>/{Cotizaciones, Reportes fotograficos, Poligonos KML}`.
    """

    class Estado(models.TextChoices):
        OK = "ok", "Conectada"
        REAUTORIZAR = "reautorizar", "Hay que volver a conectar"
        CARPETA_PERDIDA = "carpeta_perdida", "La carpeta ya no está disponible"

    cuenta_id = models.BigIntegerField(unique=True)
    correo = models.CharField(max_length=254, blank=True)
    token_cifrado = models.TextField()
    estado = models.CharField(max_length=16, choices=Estado.choices, default=Estado.OK)
    error = models.CharField(max_length=300, blank=True)
    persona = models.CharField(max_length=100, blank=True)                 # nombre de la carpeta de la persona
    carpeta_raiz_id = models.CharField(max_length=100, blank=True)         # «GRUPO ITT REPORTES» (elegida con Picker o creada por la app)
    carpetas = models.JSONField(default=dict, blank=True)                  # {persona: id, cotizaciones: id, reportes: id, poligonos: id}
    automatico = models.BooleanField(default=True)                         # enviar solo lo que se genera (reportes, KML/KMZ, cotizaciones)
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(default=timezone.now)


class EnvioDrive(models.Model):
    """Un archivo en la cola de envío a Drive: pendiente hasta que Google confirma el mismo contenido (md5)."""

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ENVIANDO = "enviando", "Enviando"
        ENVIADO = "enviado", "Enviado"
        ERROR = "error", "Con error"
        DESCARTADO = "descartado", "Descartado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    categoria = models.CharField(max_length=12)                            # cotizaciones | reportes | poligonos
    nombre = models.CharField(max_length=200)
    ruta = models.CharField(max_length=300)                                # copia local en drive_cola/, relativa a TV_HERRAMIENTAS_ARCHIVOS
    sha256 = models.CharField(max_length=64)
    clave = models.CharField(max_length=64, blank=True, db_index=True)       # huella de lo que se generó (datos + versión), para no mandar de nuevo un PDF que solo cambió de fecha interna
    bytes = models.BigIntegerField()
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    intentos = models.PositiveIntegerField(default=0)
    proximo_intento = models.DateTimeField(default=timezone.now, db_index=True)
    error = models.CharField(max_length=300, blank=True)
    drive_id = models.CharField(max_length=100, blank=True)
    drive_nombre = models.CharField(max_length=200, blank=True)            # el nombre con que quedó (puede llevar sufijo si ya había otro distinto)
    creada = models.DateTimeField(default=timezone.now)
    enviada = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["creada"]
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "categoria", "nombre", "sha256"], name="herramientas_enviodrive_unico")]


class Plano(ConAutor):
    """Un plano de la biblioteca compartida de CAD (DWG o DXF). Todo el equipo con la herramienta ve todos los planos.

    El contenido vive en sus versiones (`VersionPlano`): la vigente es la de número `revision`. `cuenta_id` es quien lo creó (con la foto de
    su nombre y correo en `autor_*`); «Modificado por» es la foto de quien guardó la versión vigente. Eliminar lo manda a la papelera
    (`eliminado`), que se vacía sola a los 30 días. El bloqueo de edición es de una sola página del editor (`bloqueo_sesion`) y caduca si su
    latido deja de llegar.
    """

    class Formato(models.TextChoices):
        DWG = "dwg", "DWG"
        DXF = "dxf", "DXF"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    nombre = models.CharField(max_length=150)                                # sin extensión
    proyecto = models.CharField(max_length=160, blank=True, db_index=True)  # proyecto o cliente
    formato = models.CharField(max_length=3, choices=Formato.choices)        # el de la versión vigente
    bytes = models.BigIntegerField(default=0)                                # tamaño de la versión vigente
    revision = models.PositiveIntegerField(default=1)                        # número de la versión vigente
    origen = models.JSONField(default=dict, blank=True)                      # {tipo: subida|nuevo|copia|convertidor|triangulacion|croquis, nombre, id}
    modificado_por_id = models.BigIntegerField(null=True, blank=True)
    modificado_por_nombre = models.CharField(max_length=150, blank=True)
    bloqueo_cuenta_id = models.BigIntegerField(null=True, blank=True)
    bloqueo_nombre = models.CharField(max_length=150, blank=True)
    bloqueo_sesion = models.CharField(max_length=64, blank=True)
    bloqueo_latido = models.DateTimeField(null=True, blank=True)
    eliminado = models.DateTimeField(null=True, blank=True, db_index=True)
    eliminado_por_id = models.BigIntegerField(null=True, blank=True)
    eliminado_por_nombre = models.CharField(max_length=150, blank=True)
    creado = models.DateTimeField(default=timezone.now)
    actualizado = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-actualizado"]

    def __str__(self):
        return self.nombre

    @property
    def archivo_nombre(self) -> str:
        return f"{self.nombre}.{self.formato}"


class VersionPlano(models.Model):
    """Una versión guardada de un plano. Cada guardado crea una; se conservan las más recientes (`cad.servicio.VERSIONES`)."""

    plano = models.ForeignKey(Plano, on_delete=models.CASCADE, related_name="versiones")
    numero = models.PositiveIntegerField()
    formato = models.CharField(max_length=3)
    ruta = models.CharField(max_length=300)                                  # relativa a TV_HERRAMIENTAS_ARCHIVOS
    bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    cuenta_id = models.BigIntegerField(null=True, blank=True)
    autor_nombre = models.CharField(max_length=150, blank=True)
    nota = models.CharField(max_length=200, blank=True)                      # p. ej. «Restaurada de la versión 3»
    creada = models.DateTimeField(default=timezone.now)
    # Los planos no tienen respaldo (decisión del propietario): tras restaurar o cambiar de servidor el archivo puede faltar.
    # `cad_reconciliar` lo marca aquí; la página la muestra como «Archivo no disponible» y nunca da error.
    sin_archivo = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-numero"]
        constraints = [models.UniqueConstraint(fields=["plano", "numero"], name="herramientas_versionplano_numero")]


class BorradorPlano(models.Model):
    """Cambios sin guardar de una persona en un plano (recuperación): NO es una versión. Uno por plano y cuenta; se borra al guardar."""

    plano = models.ForeignKey(Plano, on_delete=models.CASCADE, related_name="borradores")
    cuenta_id = models.BigIntegerField(db_index=True)
    formato = models.CharField(max_length=3)
    ruta = models.CharField(max_length=300)
    bytes = models.BigIntegerField()
    revision_base = models.PositiveIntegerField()                            # versión sobre la que se hicieron los cambios
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plano", "cuenta_id"], name="herramientas_borradorplano_unico")]
