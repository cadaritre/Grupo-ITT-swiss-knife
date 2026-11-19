"""Civil: herramientas de topografía al estilo CivilCAD dentro de CAD (docs/civil.md).

La página lee el dibujo por el canal del editor, manda aquí lo necesario y escribe de vuelta el resultado. Aquí solo se calcula, con el
motor fijado en `civil/motor/` (tvcivil): nada se guarda en la biblioteca de planos.

- Las órdenes ligeras (puntos, cuadros, invertir, borrar o agregar triángulos) responden en la misma petición.
- Las pesadas (triangular, revisar la triangulación, insertar puntos, curvas, perfil, secciones, volúmenes, curva masa) van a la cola `herramientas` (hasta 3 min, ≤ 768 MB): los datos de
  entrada pasan por un archivo temporal (las tareas solo llevan identificadores) y el resultado queda como salida de la cuenta, que se
  borra al día siguiente como las de CAD.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import uuid
from pathlib import Path

from ..cad import servicio as cad_servicio
from ..comun.errores import ErrorUsuario
from .motor import ordenes

#: Tope del cuerpo de una orden (comprimido con gzip por la página) y del JSON ya descomprimido.
MAX_COMPRIMIDO = 32 * 1024 * 1024
MAX_JSON = 160 * 1024 * 1024
#: Tamaños de plano que el servidor acepta (la página los revisa antes de mandar).
MAX_PUNTOS = 100_000
MAX_CARAS = 200_000

ORDENES = tuple(ordenes.ORDENES)
PESADAS = frozenset(ordenes.PESADAS)


def leer_cuerpo(request) -> dict:
    """El JSON de la orden: el cuerpo viene comprimido con gzip si trae `Content-Encoding: gzip`."""
    largo = int(request.META.get("CONTENT_LENGTH") or 0)
    if largo <= 0:
        raise ErrorUsuario("No llegaron datos.")
    if largo > MAX_COMPRIMIDO:
        raise ErrorUsuario("El dibujo es demasiado grande para calcularlo en línea.")
    # request.read() y no request.body: el tope general de Django (5 MB) es para formularios; este tiene el suyo.
    crudo = request.read(largo + 1)
    if len(crudo) > MAX_COMPRIMIDO:
        raise ErrorUsuario("El dibujo es demasiado grande para calcularlo en línea.")
    if (request.META.get("HTTP_CONTENT_ENCODING") or "").lower() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(crudo)) as g:
                crudo = g.read(MAX_JSON + 1)
        except OSError:
            raise ErrorUsuario("Los datos llegaron dañados. Inténtalo de nuevo.") from None
        if len(crudo) > MAX_JSON:
            raise ErrorUsuario("El dibujo es demasiado grande para calcularlo en línea.")
    try:
        datos = json.loads(crudo)
    except (ValueError, UnicodeDecodeError):
        raise ErrorUsuario("Los datos llegaron dañados. Inténtalo de nuevo.") from None
    if not isinstance(datos, dict):
        raise ErrorUsuario("Los datos llegaron dañados. Inténtalo de nuevo.")
    _revisar_tamano(datos)
    return datos


def _revisar_tamano(datos: dict) -> None:
    if len(datos.get("puntos") or []) > MAX_PUNTOS:
        raise ErrorUsuario(f"Son más de {MAX_PUNTOS:,} puntos. Divide el levantamiento.".replace(",", " "))
    for k in ("caras", "caras_terreno", "caras_proyecto"):
        if len(datos.get(k) or []) > MAX_CARAS:
            raise ErrorUsuario(f"La triangulación pasa de {MAX_CARAS:,} triángulos.".replace(",", " "))


_REQUISITOS = {
    "triangular": (("puntos", 3, "No hay puntos con elevación para triangular. Impórtalos primero."),),
    "curvas": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."),),
    "perfil": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."),),
    "secciones": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."),),
    "curva_masa": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."),),
    "revisar_tin": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."),),
    "insertar_puntos": (("caras", 1, "No hay triangulación en el dibujo. Primero triangula."), ("puntos", 1, "Elige los puntos que vas a insertar.")),
    "volumen_superficies": (("caras_terreno", 1, "Falta la triangulación del terreno."), ("caras_proyecto", 1, "Falta la triangulación de proyecto.")),
}


def revisar(orden: str, datos: dict) -> None:
    """Lo que se puede revisar antes de encolar una orden pesada: así el error sale al momento y no al terminar la tarea."""
    for clave, minimo, mensaje in _REQUISITOS.get(orden, ()):
        if len(datos.get(clave) or []) < minimo:
            raise ErrorUsuario(mensaje)
    if orden in ("secciones", "curva_masa") and datos.get("rasante") and datos.get("caras_proyecto"):
        raise ErrorUsuario("El proyecto sale de la rasante o de la triangulación de proyecto: elige una.")


def calcular(orden: str, datos: dict) -> dict:
    """Corre la orden con el motor. Los errores que la persona puede corregir salen como ErrorUsuario con su mensaje."""
    if orden not in ORDENES:
        raise ErrorUsuario("Esa acción no existe.")
    try:
        r = ordenes.ejecutar(orden, datos)
    except ordenes.ErrorOrden as exc:
        raise ErrorUsuario(str(exc)) from None
    if "documento" in r and datos.get("formato"):
        # memoria descriptiva y reporte de puntos: el contenido del motor se entrega como archivo
        from . import documentos

        formato = datos.get("formato")
        if formato not in documentos.FORMATOS:
            raise ErrorUsuario("Elige PDF o DOCX.")
        contenido, tipo, ext = documentos.archivo(r.pop("documento"), formato)
        r["archivo"], r["tipo"], r["extension"] = base64.b64encode(contenido).decode("ascii"), tipo, ext
    return r


# -- Órdenes en la cola de tareas ---------------------------------------------------------------------------------------------------

def _entrada(token: str) -> Path:
    return cad_servicio.carpeta_temporal() / f"civil-{token}.json"


def _salida(cuenta_id: int, token: str) -> Path:
    return cad_servicio.carpeta_salidas() / f"{int(cuenta_id)}-{token}.civil.json"


def guardar_entrada(datos: dict) -> str:
    token = uuid.uuid4().hex
    _entrada(token).write_text(json.dumps(datos, separators=(",", ":")), encoding="utf-8")
    return token


def correr_en_tarea(orden: str, token: str, cuenta_id: int) -> str:
    """La parte de la tarea: lee la entrada, calcula, deja el resultado como salida de la cuenta y borra la entrada."""
    ruta = _entrada(token)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErrorUsuario("Los datos de esta acción ya no están. Vuelve a intentarlo.") from None
    try:
        resultado = calcular(orden, datos)
    finally:
        ruta.unlink(missing_ok=True)
    salida = _salida(cuenta_id, token)
    temporal = salida.with_suffix(".tmp")
    temporal.write_text(json.dumps(resultado, separators=(",", ":")), encoding="utf-8")
    temporal.replace(salida)
    return token


def resultado(cuenta_id: int, token: str) -> dict | None:
    if not (len(token) == 32 and all(c in "0123456789abcdef" for c in token)):
        return None
    ruta = _salida(cuenta_id, token)
    if not ruta.is_file():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def motor() -> dict:
    """Versión y commit del motor fijado (para el pie de la ayuda y el diagnóstico)."""
    d = json.loads((Path(__file__).parent / "motor" / "MOTOR.json").read_text(encoding="utf-8"))
    return {"version": d["version"], "commit": d["commit"][:9]}


# -- Formatos de puntos guardados por cuenta ------------------------------------------------------------------------------------------

USOS_FORMATO = ("importar", "exportar")
#: Cuántos formatos guarda una cuenta por uso (importar / exportar): sobra para un despacho y evita que un guion llene la tabla.
MAX_FORMATOS = 30


def _usos(uso: str) -> str:
    if uso not in USOS_FORMATO:
        raise ErrorUsuario("Ese uso no existe.")
    return uso


def formatos_de(cuenta_id: int, uso: str) -> list[dict]:
    from ..models import FormatoPuntos

    return [{"nombre": f.nombre, "datos": f.datos} for f in FormatoPuntos.objects.filter(cuenta_id=cuenta_id, uso=_usos(uso)).order_by("nombre")]


def guardar_formato(cuenta_id: int, uso: str, nombre, datos) -> dict:
    """Guarda (o reemplaza, sin importar mayúsculas) el formato de la cuenta. `datos` pasa por el validador del motor: solo se guarda lo que
    el motor entiende. Devuelve el formato guardado."""
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    from ..models import FormatoPuntos
    from .motor.puntos import normalizar_formato

    uso = _usos(uso)
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        raise ErrorUsuario("Escribe un nombre para el formato.")
    if len(nombre) > 60:
        raise ErrorUsuario("El nombre es de 60 caracteres como máximo.")
    try:
        limpio = normalizar_formato(datos, uso)
    except ValueError as exc:
        raise ErrorUsuario(str(exc)) from None
    try:
        with transaction.atomic():
            actual = FormatoPuntos.objects.select_for_update().filter(cuenta_id=cuenta_id, uso=uso, nombre__iexact=nombre).first()
            if actual is None:
                if FormatoPuntos.objects.filter(cuenta_id=cuenta_id, uso=uso).count() >= MAX_FORMATOS:
                    raise ErrorUsuario(f"Ya tienes {MAX_FORMATOS} formatos guardados. Borra alguno para guardar otro.")
                FormatoPuntos.objects.create(cuenta_id=cuenta_id, uso=uso, nombre=nombre, datos=limpio)
            else:
                actual.nombre, actual.datos, actual.actualizado = nombre, limpio, timezone.now()
                actual.save(update_fields=["nombre", "datos", "actualizado"])
    except IntegrityError:
        raise ErrorUsuario("Ya hay un formato con ese nombre.") from None
    return {"nombre": nombre, "datos": limpio}


def borrar_formato(cuenta_id: int, uso: str, nombre) -> bool:
    from ..models import FormatoPuntos

    n, _ = FormatoPuntos.objects.filter(cuenta_id=cuenta_id, uso=_usos(uso), nombre__iexact=" ".join(str(nombre or "").split())).delete()
    return bool(n)


def al_eliminar_cuenta(cuenta_id: int) -> str | None:
    """Contrato 1.8: los formatos de puntos son de la cuenta y se borran con ella. Idempotente."""
    from ..models import FormatoPuntos

    n, _ = FormatoPuntos.objects.filter(cuenta_id=cuenta_id).delete()
    return f"Civil: se borraron {n} formato{'s' if n != 1 else ''} de puntos guardado{'s' if n != 1 else ''}." if n else None
