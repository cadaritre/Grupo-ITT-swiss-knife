"""Datos comunes de Herramientas: perfiles corporativos y perfil activo, preferencias, diccionarios y machotes.

Las personas se guardan por `cuenta_id` (entero, sin clave foránea): las cuentas son del armazón.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
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


class DocumentoCompartido(ConAutor):
    """Documento de la empresa (cotización, reporte o croquis): lo ven y editan todas las cuentas con el rol de su herramienta.

    `cuenta_id` y `autor_*` son de quien lo creó; «Modificado por» es la foto de quien guardó la revisión vigente. El bloqueo de edición
    es de una sola página del editor (`bloqueo_sesion`) y caduca si su latido deja de llegar (`comun.documentos`). Eliminar lo manda a la
    papelera (`eliminado`), que se vacía sola a los 30 días; mientras tanto su historial se conserva.
    """

    modificado_por_id = models.BigIntegerField(null=True, blank=True)
    modificado_por_nombre = models.CharField(max_length=150, blank=True, default="")
    bloqueo_cuenta_id = models.BigIntegerField(null=True, blank=True)
    bloqueo_nombre = models.CharField(max_length=150, blank=True, default="")
    bloqueo_sesion = models.CharField(max_length=64, blank=True, default="")
    bloqueo_latido = models.DateTimeField(null=True, blank=True)
    bloqueo_desde = models.DateTimeField(null=True, blank=True)
    eliminado = models.DateTimeField(null=True, blank=True, db_index=True)
    eliminado_por_id = models.BigIntegerField(null=True, blank=True)
    eliminado_por_nombre = models.CharField(max_length=150, blank=True, default="")
    # «Copia en conflicto de COT-2026-0007 (Ana López, 16/11/2026 13:05)»: lo que se guardó sin conexión y chocaba con otra versión.
    conflicto = models.CharField(max_length=300, blank=True, default="")
    # Cuentas cuyas fotos puede usar este documento: quien lo hizo o lo editó alguna vez, y lo heredado de su origen (duplicado o copia
    # en conflicto). Solo crece; recortar el historial no lo toca (`capturas.ruta_autorizada`).
    autorizados = models.JSONField(default=list, blank=True)
    # Número de cambio (secuencia `herramientas_documento_cambio`): sube con cada guardado que cambia lo que se ve en una lista, en el
    # orden en que se confirman. Es el cursor de la sincronización incremental de los teléfonos (`?desde=`).
    cambio = models.BigIntegerField(default=0, db_index=True)

    class Meta:
        abstract = True

    #: Guardados que no mueven el cursor (el latido del bloqueo, cada 15 s por página abierta).
    SIN_CAMBIO = frozenset({"bloqueo_latido"})

    def save(self, *args, **kwargs):
        campos = kwargs.get("update_fields")
        if campos is not None and set(campos) <= self.SIN_CAMBIO:
            return super().save(*args, **kwargs)
        from django.db import transaction

        with transaction.atomic():                                  # el número y el guardado se confirman juntos, en orden
            if not self._state.adding and self.pk is not None:
                # Primero el renglón y después el candado de los números de cambio, siempre en ese orden (como quien ya tiene el
                # renglón con select_for_update): al revés, una sincronización y la tarea de un PDF se esperaban una a la otra.
                type(self).objects.select_for_update().filter(pk=self.pk).values_list("pk", flat=True).first()
            self.cambio = siguiente_cambio()
            if campos is not None:
                kwargs["update_fields"] = [*campos, "cambio"]
            return super().save(*args, **kwargs)


def siguiente_cambio() -> int:
    """Siguiente número de cambio de los documentos compartidos, dentro de la transacción en curso.

    Un candado de transacción (`pg_advisory_xact_lock`) pone en fila a quienes escriben hasta que cada uno confirma: así el orden de los
    números es el orden en que se confirman, y un teléfono que ya vio el N nunca se salta un N-1 que se confirmó después.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CANDADO_CAMBIOS])
        cursor.execute("SELECT nextval('herramientas_documento_cambio')")
        return cursor.fetchone()[0]


def tocar(modelo, pk) -> None:
    """Mueve el cursor de un documento que cambió por `QuerySet.update` (p. ej. su PDF ya está al día)."""
    from django.db import transaction

    with transaction.atomic():
        if modelo.objects.select_for_update().filter(pk=pk).values_list("pk", flat=True).first() is None:     # el renglón primero
            return
        modelo.objects.filter(pk=pk).update(cambio=siguiente_cambio())


#: Llave del candado de transacción de los números de cambio (cualquier entero fijo y propio de esta app).
CANDADO_CAMBIOS = 7_461_033_901


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


class FormatoPuntos(models.Model):
    """Formato de archivo de puntos que una cuenta guardó con nombre en Civil (delimitador, columnas, decimal…), para importar o exportar.

    Chico (un JSON de unas decenas de claves) y de la cuenta: nadie más lo ve. `datos` ya pasó por el validador del motor
    (`normalizar_formato`), así que la página solo guarda lo que el motor entiende. Único por (cuenta, uso, nombre sin importar mayúsculas).
    """

    cuenta_id = models.BigIntegerField(db_index=True)
    uso = models.CharField(max_length=10)  # «importar» o «exportar»
    nombre = models.CharField(max_length=60)
    datos = models.JSONField(default=dict)
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(Lower("nombre"), "cuenta_id", "uso", name="herramientas_formatopuntos_nombre")]


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


class Machote(ConAutor):
    """Machote de concepto de cotización de la empresa (esquema `quotation_templates` del escritorio).

    Lo comparten todas las cuentas con el rol de cotizaciones: `cuenta_id` y `autor_*` son de quien lo creó y «Modificado por» de quien lo
    cambió al último. El nombre es único sin distinguir mayúsculas.
    """

    cuenta_id = models.BigIntegerField(db_index=True)
    plantilla_id = models.UUIDField(default=uuid.uuid4, unique=True)
    nombre = models.CharField(max_length=160)
    idioma = models.CharField(max_length=2, default="es")
    concepto = models.JSONField()
    anterior = models.JSONField(null=True, blank=True)               # versión previa del concepto al sobrescribir el machote
    modificado_por_id = models.BigIntegerField(null=True, blank=True)
    modificado_por_nombre = models.CharField(max_length=150, blank=True, default="")
    creado = models.DateTimeField(default=timezone.now)
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("nombre"), name="herramientas_machote_nombre_empresa")]
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


class Cotizacion(DocumentoCompartido):
    """Una cotización de la empresa. `datos` es el JSON de `QuoteData` del escritorio (mismo esquema, compatible en los dos sentidos).

    `revision` sube con cada guardado. El teléfono guarda borradores sin conexión con la revisión en que se basaron: si al
    sincronizar el servidor ya cambió, se fusiona cuando no choca y si choca se guarda como copia en conflicto (nunca pisa).

    El folio (COT-AAAA-NNNN) lo asigna el servidor (`cotizaciones.servicio.asignar_folio`) de la serie de su perfil: es único en el perfil.
    Los repetidos que venían de antes de compartir (cuando se contaba por cuenta) quedan marcados en `folio_repetido` hasta que el
    propietario les asigna uno nuevo: nunca cambian solos.

    `perfil` (de los tres documentos) es el perfil corporativo activo de quien lo creó y no cambia: cada perfil es un espacio de documentos
    aparte (`comun.documentos.del_perfil`).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta_id = models.BigIntegerField(db_index=True)
    id_cliente = models.UUIDField(null=True, blank=True)             # identidad del borrador en el teléfono: hace idempotente la sincronización
    datos = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)
    perfil = models.CharField(max_length=20, default="grupo_itt")
    folio = models.CharField(max_length=60, blank=True)
    folio_repetido = models.BooleanField(default=False)
    cliente = models.CharField(max_length=160, blank=True)
    proyecto = models.CharField(max_length=200, blank=True)
    moneda = models.CharField(max_length=3, default="MXN")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    copia_de = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copias")
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "id_cliente"], name="herramientas_cotizacion_cliente"),
                       models.UniqueConstraint(fields=["perfil", "folio"], condition=~Q(folio="") & Q(folio_repetido=False),
                                               name="herramientas_cotizacion_folio_perfil")]
        indexes = [models.Index(fields=["perfil", "cambio"], name="herramientas_cot_perfil_cambio")]
        ordering = ["-actualizada"]


class Reporte(DocumentoCompartido):
    """Reporte fotográfico de la empresa (herramientas-web.md §4.2). Se arma en el teléfono y se sincroniza como borrador.

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
        indexes = [models.Index(fields=["perfil", "cambio"], name="herramientas_rep_perfil_cambio")]
        ordering = ["-actualizada"]


class Croquis(DocumentoCompartido):
    """Croquis de ubicación de la empresa (herramientas-web.md §4.3), con autoguardado en el servidor.

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
        indexes = [models.Index(fields=["perfil", "cambio"], name="herramientas_crq_perfil_cambio")]
        ordering = ["-actualizada"]


class ContadorFolio(models.Model):
    """Último número de folio de cotización asignado en un perfil y un año (COT-AAAA-NNNN): cada perfil corporativo tiene su serie.
    Se lee y aumenta con el renglón bloqueado."""

    perfil = models.CharField(max_length=20, default="grupo_itt")
    anio = models.PositiveIntegerField()
    ultimo = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["perfil", "anio"], name="herramientas_contadorfolio_perfil_anio")]


TIPOS_DOCUMENTO = (("cotizacion", "Cotización"), ("reporte", "Reporte fotográfico"), ("croquis", "Croquis de ubicación"))


class VersionDocumento(models.Model):
    """Una revisión guardada de un documento compartido (`comun.documentos`).

    Se guarda cada revisión. Las que una misma persona guarda seguidas (en `documentos.AGRUPAR_MINUTOS`) se muestran en el historial como
    una sola: la más reciente del grupo queda `visible` y las anteriores se ocultan. Las ocultas sirven de base para fusionar lo que se
    editó sin conexión y se borran a los `documentos.INTERMEDIAS_DIAS` días.
    """

    tipo = models.CharField(max_length=10, choices=TIPOS_DOCUMENTO)
    documento_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    datos = models.JSONField()
    perfil = models.CharField(max_length=20, blank=True, default="")
    cuenta_id = models.BigIntegerField(null=True, blank=True)
    autor_nombre = models.CharField(max_length=150, blank=True, default="")
    nota = models.CharField(max_length=200, blank=True, default="")      # «Restaurada de la versión 3», «Fusionada con cambios sin conexión»…
    visible = models.BooleanField(default=True)
    inicio = models.DateTimeField(default=timezone.now)                  # primer guardado del grupo que esta versión cierra
    creada = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-revision"]
        constraints = [models.UniqueConstraint(fields=["tipo", "documento_id", "revision"], name="herramientas_versiondoc_revision")]
        indexes = [models.Index(fields=["tipo", "documento_id", "visible"], name="herramientas_versiondoc_vis")]


class BorradorRecuperacion(models.Model):
    """Cambios sin guardar de una persona que perdió el control de un documento (otra tomó el control): NO son una revisión.

    Uno por documento, cuenta y página del editor (`sesion`): dos pestañas que pierden el control guardan cada una lo suyo; lo más nuevo
    de una misma página reemplaza lo anterior. La persona los ve al abrir el documento y decide si usa o descarta cada uno.
    """

    tipo = models.CharField(max_length=10, choices=TIPOS_DOCUMENTO)
    documento_id = models.UUIDField()
    cuenta_id = models.BigIntegerField(db_index=True)
    datos = models.JSONField()
    revision_base = models.PositiveIntegerField()
    quien = models.CharField(max_length=150, blank=True, default="")      # quien tomó el control
    sesion = models.CharField(max_length=64, blank=True, default="")      # la página del editor que lo perdió
    creado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tipo", "documento_id", "cuenta_id", "sesion"], name="herramientas_recuperacion_unica")]
        ordering = ["-creado"]


class PurgaDocumentos(models.Model):
    """Hasta qué número de cambio se borraron documentos para siempre (de la papelera). Un teléfono cuyo cursor es anterior ya no verá su
    marca de eliminado: el servidor le manda la lista de los que siguen vigentes para que quite los demás."""

    tipo = models.CharField(max_length=10, unique=True)
    hasta = models.BigIntegerField(default=0)


class AvisoDocumento(models.Model):
    """Aviso para una cuenta sobre un documento compartido: tomaron el control, o lo que guardó quedó junto a una copia en conflicto."""

    cuenta_id = models.BigIntegerField(db_index=True)
    tipo = models.CharField(max_length=10, choices=TIPOS_DOCUMENTO)
    documento_id = models.UUIDField()
    texto = models.CharField(max_length=400)
    creado = models.DateTimeField(default=timezone.now)
    visto = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado"]


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
    sesion = models.CharField(max_length=64, blank=True, default="")         # la página del editor que lo escribió

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plano", "cuenta_id"], name="herramientas_borradorplano_unico")]


class PlantillaCad(models.Model):
    """«Mis plantillas» de CAD: un plano de la biblioteca que una persona marcó para insertar sus presentaciones (su marco y su rótulo)
    con «Desde plantilla…». La marca es de la cuenta; el plano sigue siendo de la biblioteca compartida (lo ven todos). «Guardar como
    plantilla…» crea el plano en el proyecto «Plantillas» y lo marca aquí. `usada`: la última vez que se insertó desde ella (orden).
    """

    cuenta_id = models.BigIntegerField(db_index=True)
    plano = models.ForeignKey(Plano, on_delete=models.CASCADE, related_name="plantillas")
    creada = models.DateTimeField(default=timezone.now)
    usada = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "plano"], name="herramientas_plantillacad_unica")]


class FuenteCad(models.Model):
    """Una fuente SHX de la empresa para CAD (txt, romans, simplex, isocp, bigfonts…), subida por quien administra desde su AutoCAD.

    El editor web no trae las SHX: la página del plano las registra en el editor cada vez que lo abre. El archivo vive en
    `TV_HERRAMIENTAS_ARCHIVOS/fuentes/` (fuera de `cad/`, así entra en el respaldo normal: son pocos kB y no se vuelven a subir tras un
    cambio de servidor). Son de la empresa: solo se sirven a cuentas con CAD, nunca públicas.
    """

    class Tipo(models.TextChoices):
        FORMAS = "shapes", "Fuente de trazos"
        UNIFONT = "unifont", "Fuente Unicode"
        BIGFONT = "bigfont", "Fuente asiática (bigfont)"

    nombre = models.CharField(max_length=64, unique=True)                   # «txt.shx», en minúsculas: el que usan los estilos de texto
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    glifos = models.PositiveIntegerField(null=True, blank=True)              # lo que dice el editor al registrarla
    bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    subida_por_nombre = models.CharField(max_length=150, blank=True)
    actualizada = models.DateTimeField(default=timezone.now)
    error = models.CharField(max_length=200, blank=True)                     # si el editor la rechazó en la última carga

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
