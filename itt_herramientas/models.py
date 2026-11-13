"""Datos comunes de Herramientas: perfiles corporativos, accesos, preferencias, diccionarios y machotes.

Las personas se guardan por `cuenta_id` (entero, sin clave foránea): las cuentas son del armazón.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

CLAVES_PERFIL = (("grupo_itt", "Grupo ITT"), ("tresvizo", "TresVizo"))


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


class AccesoPerfil(models.Model):
    """Perfil que una cuenta puede usar. Sin renglones, la cuenta no tiene perfil (lo concede el propietario)."""

    cuenta_id = models.BigIntegerField(db_index=True)
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name="accesos")
    predeterminado = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta_id", "perfil"], name="herramientas_acceso_unico")]


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
    creado = models.DateTimeField(default=timezone.now)
    actualizado = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cuenta_id", "plantilla_id"], name="herramientas_machote_id"),
            models.UniqueConstraint(fields=["cuenta_id", "nombre"], name="herramientas_machote_nombre"),
        ]
        ordering = ["nombre"]
