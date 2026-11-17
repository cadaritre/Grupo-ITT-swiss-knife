"""CAD: la biblioteca compartida de planos (DWG y DXF) con versiones, bloqueo de edición, borradores de recuperación y papelera.

Reglas:
- Biblioteca de la empresa: quien tiene la herramienta ve todos los planos. Cada plano guarda la foto de quien lo creó y de quien
  guardó la versión vigente.
- Cada guardado es una versión nueva; se conservan las `limites()["versiones"]` más recientes de cada plano. Nada se pisa sin avisar:
  un guardado sobre una versión que ya no es la vigente, o sin el bloqueo, se rechaza (`Conflicto`) y se ofrece como copia.
- El bloqueo es de una página del editor (`sesion`), con latido; si el latido deja de llegar caduca solo. Quien administra lo puede tomar.
- Un borrador de recuperación NO es una versión: son los cambios sin guardar de una persona, para no perderlos si se cierra la página.
- Eliminar manda a la papelera; a los 30 días `limpiar()` (tv_limpiar) borra el plano y sus archivos.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import unicodedata
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from tvplataforma import ajustes, permisos

from ..comun import autoria
from ..comun.errores import ErrorUsuario
from ..models import BorradorPlano, Plano, VersionPlano

log = logging.getLogger("itt_herramientas.cad")

FORMATOS = ("dwg", "dxf")
TIPOS_MIME = {"dwg": "image/vnd.dwg", "dxf": "image/vnd.dxf"}
#: Límites por omisión. Se cambian con `TV_HERRAMIENTAS_LIMITES["cad"]` (mismas claves).
#: - bytes: un plano (subida, guardado o borrador). 50 MiB, como las demás herramientas; el editor del navegador abre bien hasta ahí.
#: - versiones: las más recientes que se guardan por plano.
#: - biblioteca_bytes: todo lo de CAD en disco (versiones vigentes y anteriores, papelera y borradores). 18 GiB: CAD tiene su propia
#:   reserva de 20 GB en el disco de 100 GB del servidor, aparte de los 12 GB de Herramientas (Portal, docs/arquitectura.md §9.1 y §13);
#:   los 2 GB restantes son para salidas (PDF, conversiones, ZIP) y temporales, así la biblioteca llena no deja a tv-salud en «crítico».
#:   Por decisión del propietario (15-11-2026) los planos NO van al respaldo cifrado en R2: un desastre o un cambio de servidor puede
#:   perderlos. Por eso la biblioteca tolera archivos que faltan (`reconciliar`, `archivo_disponible`) y nunca da error por ello.
LIMITES = {"bytes": 50 * 1024 * 1024, "versiones": 30, "biblioteca_bytes": 18 * 1024 ** 3}
#: Desde qué fracción del tope la línea de espacio de la biblioteca se vuelve aviso.
AVISO_ESPACIO = 0.9
#: Versiones que se conservan siempre de cada plano, aun si la biblioteca se llena (se recortan las más viejas primero).
VERSIONES_MINIMAS = 5
#: Segundos sin latido tras los cuales el bloqueo caduca (el editor late cada 30 s).
BLOQUEO_SEGUNDOS = 90
PAPELERA_DIAS = 30
BORRADOR_DIAS = 30
SALIDAS_HORAS = 24
NOMBRE_MAX = 150
PROYECTO_MAX = 160
_SESION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
#: Lo devuelve `_bloqueo_de_otro` cuando el bloqueo es de la misma cuenta en otra página (otra pestaña u otro equipo).
OTRA_VENTANA = "\x00otra-ventana"


def _quien(otro: str) -> str:
    return "Tú, en otra ventana" if otro == OTRA_VENTANA else otro


class Conflicto(ErrorUsuario):
    """El guardado no se puede aplicar sobre la versión vigente; `motivo` dice por qué y la página lo ofrece como copia."""

    def __init__(self, mensaje: str, motivo: str, **datos):
        super().__init__(mensaje)
        self.motivo = motivo
        self.datos = datos


class SinPermiso(ErrorUsuario):
    pass


# -- Permisos ------------------------------------------------------------------------------------------------------------------

def _rol(usuario, *roles) -> bool:
    return any(permisos.tiene_rol(usuario, "herramientas", r) for r in roles)


def puede_ver(usuario) -> bool:
    return _rol(usuario, "cad", "admin", "supervision")


def puede_editar(usuario) -> bool:
    return _rol(usuario, "cad", "admin")


def es_admin(usuario) -> bool:
    """Administra la biblioteca: elimina cualquier plano, vacía la papelera y toma el control de un plano en edición."""
    return _rol(usuario, "admin")


def puede_eliminar(usuario, plano: Plano) -> bool:
    return es_admin(usuario) or (puede_editar(usuario) and plano.cuenta_id == usuario.pk)


# -- Rutas y límites -----------------------------------------------------------------------------------------------------------

def raiz() -> Path:
    return Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))


def carpeta(plano: Plano | uuid.UUID | str) -> Path:
    pk = plano.pk if isinstance(plano, Plano) else plano
    return raiz() / "cad" / "planos" / str(pk)


def carpeta_temporal() -> Path:
    c = raiz() / "cad" / "tmp"
    c.mkdir(parents=True, exist_ok=True)
    return c


def carpeta_salidas() -> Path:
    c = raiz() / "cad" / "salidas"
    c.mkdir(parents=True, exist_ok=True)
    return c


def limites() -> dict:
    propios = (getattr(settings, "TV_HERRAMIENTAS_LIMITES", None) or {}).get("cad") or {}
    return {**LIMITES, **{k: int(v) for k, v in propios.items() if k in LIMITES}}


def megas(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


# -- Nombres y formatos --------------------------------------------------------------------------------------------------------

def nombre_limpio(nombre: str, por_omision: str = "Plano sin nombre") -> str:
    """Nombre para mostrar y para descargar: sin extensión DWG/DXF, sin caracteres de control ni los que Windows no admite."""
    texto = unicodedata.normalize("NFC", str(nombre or ""))
    texto = re.sub(r"\.(dwg|dxf)$", "", texto.strip(), flags=re.I)
    texto = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', " ", texto)
    texto = " ".join(texto.split()).strip(" .")
    return texto[:NOMBRE_MAX].strip(" .") or por_omision


def proyecto_limpio(texto: str) -> str:
    return " ".join(str(texto or "").split())[:PROYECTO_MAX]


def detectar_formato(cabecera: bytes) -> str | None:
    """«dwg», «dxf» o None, por el contenido (no por la extensión)."""
    if re.match(rb"AC1\d{3}", cabecera[:6]) or re.match(rb"AC1\.\d", cabecera[:6]) or cabecera[:6] in (b"AC2.10", b"AC2.21", b"AC2.22"):
        return "dwg"
    if cabecera.startswith(b"AutoCAD Binary DXF"):
        return "dxf"
    inicio = cabecera[:8192]
    if b"\x00" in inicio:
        return None
    texto = inicio.lstrip(b"\xef\xbb\xbf \t\r\n")
    if re.match(rb"(0|999)\s*\r?\n", texto) and (b"SECTION" in inicio or b"HEADER" in inicio):
        return "dxf"
    return None


def formato_de_archivo(ruta: Path, esperado: str | None = None) -> str:
    """Comprueba que el archivo sea un DWG o DXF (y, si se pide, del formato esperado)."""
    with open(ruta, "rb") as f:
        cabecera = f.read(8192)
    formato = detectar_formato(cabecera)
    if formato is None:
        raise ErrorUsuario("El archivo no es un DWG ni un DXF válido.")
    if esperado and formato != esperado:
        raise ErrorUsuario(f"El archivo es un {formato.upper()}, no un {esperado.upper()}.")
    return formato


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1024 * 1024), b""):
            h.update(trozo)
    return h.hexdigest()


def recibir_cuerpo(request, maximo: int | None = None) -> Path:
    """Escribe el cuerpo de la petición (bytes del plano) en un temporal sin cargarlo entero en memoria. Aplica el límite."""
    maximo = maximo or limites()["bytes"]
    try:
        declarado = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declarado = 0
    if declarado > maximo:
        raise ErrorUsuario(f"El plano pesa más de {megas(maximo)}, el máximo en la web.")
    destino = carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    total = 0
    try:
        with open(destino, "wb") as f:
            while trozo := request.read(1024 * 1024):
                total += len(trozo)
                if total > maximo:
                    raise ErrorUsuario(f"El plano pesa más de {megas(maximo)}, el máximo en la web.")
                f.write(trozo)
        if not total:
            raise ErrorUsuario("No llegó ningún archivo.")
    except BaseException:
        destino.unlink(missing_ok=True)
        raise
    return destino


# -- Espacio -------------------------------------------------------------------------------------------------------------------

def uso() -> int:
    """Bytes de CAD en disco (versiones y borradores; las versiones sin archivo no cuentan)."""
    v = VersionPlano.objects.filter(sin_archivo__isnull=True).aggregate(t=Sum("bytes"))["t"] or 0
    b = BorradorPlano.objects.aggregate(t=Sum("bytes"))["t"] or 0
    return int(v) + int(b)


def uso_detalle() -> dict:
    """Bytes de la biblioteca por parte: vigentes, versiones anteriores, papelera y borradores (las versiones sin archivo no ocupan)."""
    vivas = VersionPlano.objects.filter(sin_archivo__isnull=True)
    activos = vivas.filter(plano__eliminado__isnull=True)
    vigentes = activos.filter(numero=F("plano__revision")).aggregate(t=Sum("bytes"))["t"] or 0
    anteriores = (activos.aggregate(t=Sum("bytes"))["t"] or 0) - vigentes
    papelera = vivas.filter(plano__eliminado__isnull=False).aggregate(t=Sum("bytes"))["t"] or 0
    borradores = BorradorPlano.objects.aggregate(t=Sum("bytes"))["t"] or 0
    return {"vigentes": int(vigentes), "anteriores": int(anteriores), "papelera": int(papelera), "borradores": int(borradores)}


def espacio() -> dict:
    """Lo que muestra la biblioteca: usado, tope, fracción y si ya está cerca del tope."""
    d = uso_detalle()
    usado, tope = sum(d.values()), limites()["biblioteca_bytes"]
    fraccion = usado / tope if tope else 0.0
    return {"usado": usado, "tope": tope, "fraccion": fraccion, "porcentaje": min(100, round(fraccion * 100)), "aviso": fraccion >= AVISO_ESPACIO, "detalle": d}


# -- Archivos que faltan (los planos no tienen respaldo) ----------------------------------------------------------------------

def archivo_disponible(v: VersionPlano | None) -> bool:
    return v is not None and v.sin_archivo is None and ruta_version(v).is_file()


#: Un archivo sin renglón más nuevo que esto puede ser un guardado en curso (se mueve antes de confirmar la transacción): no es huérfano.
HUERFANO_HORAS = 1


def huerfanos() -> list[Path]:
    """Lo que hay en `cad/planos/` sin renglón en la base: carpetas de planos que no existen y archivos de versiones o borradores
    que no son de ninguna versión ni borrador (tras restaurar la base a un punto anterior con el disco intacto)."""
    import time

    base = raiz() / "cad" / "planos"
    if not base.is_dir():
        return []
    limite = time.time() - HUERFANO_HORAS * 3600
    planos = {str(pk) for pk in Plano.objects.values_list("pk", flat=True)}
    rutas = set(VersionPlano.objects.values_list("ruta", flat=True)) | set(BorradorPlano.objects.values_list("ruta", flat=True))
    salida = []
    for d in base.iterdir():
        if not d.is_dir():
            salida.append(d)
            continue
        if d.name not in planos:
            if all(f.stat().st_mtime < limite for f in d.rglob("*") if f.is_file()):
                salida.append(d)
            continue
        for f in d.rglob("*"):
            if f.is_file() and str(f.relative_to(raiz())) not in rutas and f.stat().st_mtime < limite:
                salida.append(f)
    return salida


def _bytes_de(ruta: Path) -> int:
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file()) if ruta.is_dir() else ruta.stat().st_size


def reconciliar(simular: bool = False, borrar_huerfanos: bool = False) -> dict:
    """Compara la base con el disco (tras restaurar o cambiar de servidor). Idempotente.

    - Una versión cuyo archivo no está se marca `sin_archivo` (y se desmarca si el archivo volvió).
    - Un borrador sin archivo se borra (solo era una copia de recuperación).
    - Lo que está en el disco sin renglón (`huerfanos`) se cuenta y, con `borrar_huerfanos`, se borra. Nunca solo: como los planos no
      tienen respaldo, esos archivos pueden ser la única copia de versiones posteriores al punto restaurado; quien administra decide.
    Devuelve las cuentas: versiones y planos (cuya versión vigente falta) marcados ahora, los que ya estaban, los que se desmarcaron y
    los huérfanos (cuántos y cuánto ocupan).
    """
    ahora = timezone.now()
    marcadas = ya = desmarcadas = 0
    planos_nuevos: set = set()
    planos_ya: set = set()
    for v in VersionPlano.objects.select_related("plano").iterator():
        existe = ruta_version(v).is_file()
        vigente = v.numero == v.plano.revision
        if not existe and v.sin_archivo is None:
            marcadas += 1
            if vigente:
                planos_nuevos.add(v.plano_id)
            if not simular:
                VersionPlano.objects.filter(pk=v.pk).update(sin_archivo=ahora)
        elif not existe:
            ya += 1
            if vigente:
                planos_ya.add(v.plano_id)
        elif v.sin_archivo is not None:
            desmarcadas += 1
            if not simular:
                VersionPlano.objects.filter(pk=v.pk).update(sin_archivo=None)
    borradores = 0
    for b in BorradorPlano.objects.all():
        if not (raiz() / b.ruta).is_file():
            borradores += 1
            if not simular:
                b.delete()
    sobran = huerfanos()
    bytes_sobran = sum(_bytes_de(h) for h in sobran)
    if borrar_huerfanos and not simular:
        for h in sobran:
            shutil.rmtree(h, ignore_errors=True) if h.is_dir() else h.unlink(missing_ok=True)
    return {"versiones_marcadas": marcadas, "versiones_ya": ya, "versiones_desmarcadas": desmarcadas, "planos_marcados": len(planos_nuevos),
            "planos_ya": len(planos_ya), "borradores_borrados": borradores, "huerfanos": len(sobran), "huerfanos_bytes": bytes_sobran,
            "huerfanos_borrados": len(sobran) if borrar_huerfanos and not simular else 0}


#: Clave del candado de PostgreSQL que hace de uno en uno el cálculo del espacio y el recorte («tvcad» en ASCII).
_CANDADO_ESPACIO = 0x7476636164


def _candado_espacio() -> None:
    """Candado de transacción: dos guardados a la vez no pasan los dos el tope ni recortan las mismas versiones."""
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)", [_CANDADO_ESPACIO])


def _sin_espacio(tope: int) -> ErrorUsuario:
    return ErrorUsuario(f"La biblioteca de planos llegó a su límite ({tope / 1024 ** 3:.0f} GB). Vacía la papelera o elimina planos que ya no uses.")


def asegurar_espacio(nuevos: int, conservar=(), recortar: bool = True) -> None:
    """Si lo nuevo no cabe en la biblioteca, recorta las versiones más viejas (siempre deja `VERSIONES_MINIMAS` de cada plano).

    Va DENTRO de la transacción de lo que se guarda, después de comprobar bloqueo y versión: así un guardado rechazado no recorta nada,
    y si la transacción se deshace los archivos siguen ahí (`_borrar_version` los borra al confirmar). `conservar`: claves de versiones
    que no se tocan (la que se restaura). Con `recortar=False` (borradores) no se borra historia: si no cabe, se niega.
    Si aun así no cabe, se niega con un mensaje claro: nunca se borra la versión vigente ni un borrador.
    """
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("asegurar_espacio va dentro de una transacción")
    _candado_espacio()
    tope = limites()["biblioteca_bytes"]
    usado = uso()
    if usado + nuevos <= tope:
        return
    if not recortar:
        raise _sin_espacio(tope)
    # una sola consulta: de cada plano, las versiones más allá de las VERSIONES_MINIMAS más recientes, de la más vieja a la más nueva
    candidatas = (VersionPlano.objects.annotate(orden=Window(RowNumber(), partition_by=[F("plano_id")], order_by=F("numero").desc()))
                  .filter(orden__gt=VERSIONES_MINIMAS).exclude(pk__in=list(conservar)).order_by("creada"))
    for v in candidatas:
        if usado + nuevos <= tope:
            break
        if v.sin_archivo is None:
            usado -= v.bytes
        _borrar_version(v)
    if usado + nuevos > tope:
        raise _sin_espacio(tope)


def _borrar_version(v: VersionPlano) -> None:
    """Borra el renglón ya y el archivo cuando la transacción se confirme: una transacción deshecha nunca deja renglones sin archivo."""
    ruta = raiz() / v.ruta
    v.delete()
    transaction.on_commit(lambda: ruta.unlink(missing_ok=True))


def _recortar_versiones(plano: Plano) -> None:
    maximo = limites()["versiones"]
    for v in VersionPlano.objects.filter(plano=plano).order_by("-numero")[maximo:]:
        _borrar_version(v)


# -- Crear y guardar -----------------------------------------------------------------------------------------------------------

def _mover_a_version(plano: Plano, temporal: Path, formato: str, numero: int) -> tuple[str, int, str]:
    destino_dir = carpeta(plano)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"v{numero}.{formato}"
    sha = _sha256(temporal)
    tam = temporal.stat().st_size
    os.replace(temporal, destino)
    return str(destino.relative_to(raiz())), tam, sha


def _nueva_version(plano: Plano, temporal: Path, formato: str, usuario, nota: str = "") -> VersionPlano:
    numero = (VersionPlano.objects.filter(plano=plano).order_by("-numero").values_list("numero", flat=True).first() or 0) + 1
    ruta, tam, sha = _mover_a_version(plano, temporal, formato, numero)
    nombre, _ = autoria.de_usuario(usuario)
    v = VersionPlano.objects.create(plano=plano, numero=numero, formato=formato, ruta=ruta, bytes=tam, sha256=sha,
                                    cuenta_id=getattr(usuario, "pk", None), autor_nombre=nombre, nota=nota[:200])
    plano.revision, plano.formato, plano.bytes = numero, formato, tam
    plano.modificado_por_id, plano.modificado_por_nombre = getattr(usuario, "pk", None), nombre
    plano.actualizado = timezone.now()
    plano.save(update_fields=["revision", "formato", "bytes", "modificado_por_id", "modificado_por_nombre", "actualizado"])
    _recortar_versiones(plano)
    return v


def crear(usuario, temporal: Path, nombre: str, proyecto: str = "", origen: dict | None = None, formato: str | None = None) -> Plano:
    """Plano nuevo con su versión 1. `temporal` se mueve (no se copia)."""
    formato = formato_de_archivo(temporal, formato)
    tam = temporal.stat().st_size
    if tam > limites()["bytes"]:
        temporal.unlink(missing_ok=True)
        raise ErrorUsuario(f"El plano pesa más de {megas(limites()['bytes'])}, el máximo en la web.")
    with transaction.atomic():
        asegurar_espacio(tam)
        p = Plano(cuenta_id=usuario.pk, nombre=nombre_limpio(nombre), proyecto=proyecto_limpio(proyecto), formato=formato, bytes=tam,
                  revision=0, origen=origen or {"tipo": "subida"})
        p.save()
        _nueva_version(p, temporal, formato, usuario, nota=_nota_origen(p.origen))
    return p


def _nota_origen(origen: dict) -> str:
    return {"subida": "Subido", "nuevo": "Plano nuevo", "copia": "Copia", "convertidor": "Desde el convertidor",
            "triangulacion": "Desde triangulación", "croquis": "Desde el croquis de ubicación"}.get((origen or {}).get("tipo", ""), "")


def crear_desde_ruta(usuario, origen_ruta: Path, nombre: str, proyecto: str = "", origen: dict | None = None) -> Plano:
    """Copia un archivo existente (salida de otra herramienta) a la biblioteca."""
    if not origen_ruta.is_file():
        raise ErrorUsuario("El archivo ya no existe. Vuelve a generarlo.")
    temporal = carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    shutil.copyfile(origen_ruta, temporal)
    try:
        return crear(usuario, temporal, nombre, proyecto, origen)
    finally:
        temporal.unlink(missing_ok=True)


PLANTILLAS = Path(__file__).resolve().parent / "plantillas"


def crear_vacio(usuario, nombre: str, proyecto: str, formato: str) -> Plano:
    if formato not in FORMATOS:
        raise ErrorUsuario("Elige DWG o DXF.")
    temporal = carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    shutil.copyfile(PLANTILLAS / f"vacio.{formato}", temporal)
    try:
        return crear(usuario, temporal, nombre or "Plano nuevo", proyecto, {"tipo": "nuevo"}, formato)
    finally:
        temporal.unlink(missing_ok=True)


def version_vigente(plano: Plano) -> VersionPlano | None:
    return VersionPlano.objects.filter(plano=plano, numero=plano.revision).first()


def ruta_version(v: VersionPlano) -> Path:
    return raiz() / v.ruta


def guardar(plano_id, usuario, sesion: str, revision_base: int, temporal: Path) -> Plano:
    """Guarda una versión nueva desde el editor. Exige el bloqueo de ESTA página y que la versión base sea la vigente."""
    formato = formato_de_archivo(temporal)
    with transaction.atomic():
        p = Plano.objects.select_for_update().filter(pk=plano_id).first()
        if p is None or p.eliminado:
            raise Conflicto("Este plano se eliminó mientras lo editabas. Guárdalo como copia para no perder tus cambios.", "eliminado")
        ahora = timezone.now()
        otro = _bloqueo_de_otro(p, usuario, sesion, ahora)
        if otro:
            texto = ("Este plano se está editando en otra ventana tuya. Guarda estos cambios como copia o cierra la otra ventana." if otro == OTRA_VENTANA
                     else f"{otro} tomó el control de este plano. Guarda tus cambios como copia.")
            raise Conflicto(texto, "bloqueo", quien=_quien(otro))
        if int(revision_base) != p.revision:
            raise Conflicto(f"{p.modificado_por_nombre or 'Alguien'} guardó una versión nueva mientras editabas. Guarda tus cambios como copia.",
                            "cambio", revision=p.revision, quien=p.modificado_por_nombre)
        asegurar_espacio(temporal.stat().st_size)          # solo un guardado que sí se aplica puede recortar versiones
        _nueva_version(p, temporal, formato, usuario)
        _tomar(p, usuario, sesion, ahora)
        BorradorPlano.objects.filter(plano=p, cuenta_id=usuario.pk).delete()
    _borrar_borrador_archivo(p, usuario.pk)
    return p


def guardar_copia(plano_id, usuario, temporal: Path, nombre: str | None = None) -> Plano:
    """Lo que no se pudo guardar sobre el plano queda como un plano nuevo («… (copia)»), con su origen."""
    original = Plano.objects.filter(pk=plano_id).first()
    base = nombre_limpio(nombre) if nombre else (f"{original.nombre} (copia)" if original else "Plano recuperado")
    origen = {"tipo": "copia", "id": str(original.pk), "nombre": original.nombre} if original else {"tipo": "copia"}
    return crear(usuario, temporal, base, original.proyecto if original else "", origen)


def duplicar(plano: Plano, usuario) -> Plano:
    v = version_vigente(plano)
    if v is None:
        raise ErrorUsuario("Este plano no tiene una versión guardada.")
    if not archivo_disponible(v):
        raise ErrorUsuario("El archivo de este plano no está en el servidor: no se puede duplicar.")
    return crear_desde_ruta(usuario, ruta_version(v), f"{plano.nombre} (copia)", plano.proyecto, {"tipo": "copia", "id": str(plano.pk), "nombre": plano.nombre})


def restaurar_version(plano_id, numero: int, usuario, sesion: str = "") -> Plano:
    """La versión `numero` pasa a ser la vigente como una versión NUEVA (la historia no se reescribe)."""
    with transaction.atomic():
        p = Plano.objects.select_for_update().filter(pk=plano_id, eliminado__isnull=True).first()
        if p is None:
            raise ErrorUsuario("Este plano ya no existe.")
        otro = _bloqueo_de_otro(p, usuario, sesion, timezone.now())
        if otro:
            raise ErrorUsuario("Este plano está abierto para editar en otra ventana tuya. Ciérrala primero." if otro == OTRA_VENTANA
                               else f"{otro} está editando este plano. Espera a que termine para restaurar una versión.")
        v = VersionPlano.objects.filter(plano=p, numero=numero).first()
        if v is None:
            raise ErrorUsuario("Esa versión ya no existe.")
        if not archivo_disponible(v):
            raise ErrorUsuario("El archivo de esa versión no está en el servidor: no se puede restaurar.")
        if v.numero == p.revision:
            raise ErrorUsuario("Esa ya es la versión vigente.")
        # Primero la copia y después el espacio, sin tocar la versión que se restaura: con la biblioteca llena el recorte nunca la alcanza.
        temporal = carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
        try:
            shutil.copyfile(ruta_version(v), temporal)
            asegurar_espacio(v.bytes, conservar={v.pk})
            _nueva_version(p, temporal, v.formato, usuario, nota=f"Restaurada de la versión {v.numero}")
        finally:
            temporal.unlink(missing_ok=True)
    return p


def renombrar(plano: Plano, nombre: str, proyecto: str) -> Plano:
    plano.nombre = nombre_limpio(nombre, plano.nombre)
    plano.proyecto = proyecto_limpio(proyecto)
    plano.save(update_fields=["nombre", "proyecto"])
    return plano


# -- Bloqueo ---------------------------------------------------------------------------------------------------------------------

def sesion_valida(sesion: str) -> bool:
    return bool(_SESION.match(sesion or ""))


def bloqueo_activo(p: Plano, ahora=None) -> bool:
    ahora = ahora or timezone.now()
    return bool(p.bloqueo_sesion and p.bloqueo_latido and p.bloqueo_latido > ahora - timedelta(seconds=BLOQUEO_SEGUNDOS))


def _bloqueo_de_otro(p: Plano, usuario, sesion: str, ahora) -> str:
    """Nombre de quien tiene el bloqueo vigente si NO es esta página; "" si está libre o es de esta página."""
    if not bloqueo_activo(p, ahora) or p.bloqueo_sesion == sesion:
        return ""
    if p.bloqueo_cuenta_id == usuario.pk:
        return OTRA_VENTANA
    return p.bloqueo_nombre or "Otra persona"


def _tomar(p: Plano, usuario, sesion: str, ahora) -> None:
    p.bloqueo_cuenta_id, p.bloqueo_nombre = usuario.pk, autoria.de_usuario(usuario)[0]
    p.bloqueo_sesion, p.bloqueo_latido = sesion, ahora
    p.save(update_fields=["bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion", "bloqueo_latido"])


def estado_bloqueo(p: Plano, usuario, sesion: str = "") -> dict:
    ahora = timezone.now()
    if not bloqueo_activo(p, ahora):
        return {"libre": True, "mio": False, "quien": "", "otra_ventana": False}
    mio = p.bloqueo_sesion == sesion and bool(sesion)
    return {"libre": False, "mio": mio, "quien": "" if mio else (p.bloqueo_nombre or "Otra persona"),
            "otra_ventana": not mio and p.bloqueo_cuenta_id == usuario.pk, "desde": p.bloqueo_latido.isoformat()}


def bloqueo(plano_id, usuario, sesion: str, accion: str, revision: int | None = None) -> dict:
    """tomar | latido | soltar | forzar. Devuelve el estado para la página. «forzar» es de quien administra (o de la misma cuenta).

    `revision` (latido): la versión sobre la que edita la página. Si su bloqueo caducó y mientras tanto alguien guardó otra versión, el
    latido NO lo retoma: responde `perdido` con `motivo: "cambio"` para que la página lo diga antes de que se siga dibujando sobre una
    versión vieja.
    """
    if not sesion_valida(sesion):
        raise ErrorUsuario("La página del editor no es válida. Recárgala.")
    with transaction.atomic():
        p = Plano.objects.select_for_update().filter(pk=plano_id).first()
        if p is None or p.eliminado:
            return {"libre": False, "mio": False, "quien": "", "eliminado": True}
        ahora = timezone.now()
        otro = _bloqueo_de_otro(p, usuario, sesion, ahora)
        if accion == "soltar":
            if p.bloqueo_sesion == sesion:
                p.bloqueo_cuenta_id, p.bloqueo_nombre, p.bloqueo_sesion, p.bloqueo_latido = None, "", "", None
                p.save(update_fields=["bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion", "bloqueo_latido"])
            return {"libre": True, "mio": False, "quien": "", "revision": p.revision}
        if accion == "latido":
            if otro:
                return {**estado_bloqueo(p, usuario, sesion), "perdido": True, "revision": p.revision}
            sigue_mio = bloqueo_activo(p, ahora) and p.bloqueo_sesion == sesion
            if not sigue_mio and revision is not None and revision != p.revision:
                return {**estado_bloqueo(p, usuario, sesion), "perdido": True, "motivo": "cambio", "revision": p.revision,
                        "quien": p.modificado_por_nombre or "Alguien"}
            _tomar(p, usuario, sesion, ahora)
            return {**estado_bloqueo(p, usuario, sesion), "revision": p.revision}
        if accion == "forzar":
            if otro and not (es_admin(usuario) or p.bloqueo_cuenta_id == usuario.pk):
                raise SinPermiso("Solo quien administra las herramientas puede tomar el control de un plano que otra persona edita.")
            anterior, cuenta_anterior = otro, p.bloqueo_cuenta_id
            _tomar(p, usuario, sesion, ahora)
            if anterior and anterior != OTRA_VENTANA:
                # en el registro, cuentas y no nombres de personas
                log.info("CAD: la cuenta %s tomó el control del plano %s (lo editaba la cuenta %s)", usuario.pk, p.pk, cuenta_anterior)
            return {**estado_bloqueo(p, usuario, sesion), "revision": p.revision, "tomado_de": _quien(anterior) if anterior else ""}
        if accion == "tomar":
            if otro:
                return {**estado_bloqueo(p, usuario, sesion), "revision": p.revision}
            _tomar(p, usuario, sesion, ahora)
            return {**estado_bloqueo(p, usuario, sesion), "revision": p.revision}
    raise ErrorUsuario("Acción desconocida.")


# -- Borradores de recuperación ------------------------------------------------------------------------------------------------

def _ruta_borrador(plano: Plano, cuenta_id: int, formato: str) -> Path:
    return carpeta(plano) / "borradores" / f"{int(cuenta_id)}.{formato}"


def _borrar_borrador_archivo(plano: Plano, cuenta_id: int) -> None:
    for f in FORMATOS:
        _ruta_borrador(plano, cuenta_id, f).unlink(missing_ok=True)


def guardar_borrador(plano: Plano, usuario, revision_base: int, temporal: Path, sesion: str = "", previo_visto: str = "") -> BorradorPlano:
    """Guarda (o reemplaza) el borrador de recuperación de esta cuenta en este plano.

    Un borrador que dejó OTRA página (el de recuperación que se ofrece al abrir) no se pisa en silencio: solo lo reemplaza la página que
    lo vio y decidió (`previo_visto` = su `actualizado`, que la página recibió al abrir) o la misma página que lo escribió (`sesion`).
    Si no, `Conflicto("borrador_pendiente")`: la página pide recuperar, descartar o guardar como copia.
    """
    formato = formato_de_archivo(temporal)
    with transaction.atomic():
        previo = BorradorPlano.objects.select_for_update().filter(plano=plano, cuenta_id=usuario.pk).first()
        if previo is not None and previo.sesion != sesion and previo.actualizado.isoformat() != previo_visto:
            raise Conflicto("Tienes cambios sin guardar de otra vez en este plano. Recupéralos, descártalos o guárdalos como copia antes de seguir.",
                            "borrador_pendiente", actualizado=previo.actualizado.isoformat())
        # un borrador nunca recorta la historia de nadie: si no cabe, no hay borrador (la página lo dice y el trabajo sigue en el editor)
        asegurar_espacio(max(0, temporal.stat().st_size - (previo.bytes if previo else 0)), recortar=False)
        destino = _ruta_borrador(plano, usuario.pk, formato)
        destino.parent.mkdir(parents=True, exist_ok=True)
        tam = temporal.stat().st_size
        _borrar_borrador_archivo(plano, usuario.pk)
        os.replace(temporal, destino)
        b, _ = BorradorPlano.objects.update_or_create(plano=plano, cuenta_id=usuario.pk, defaults={
            "formato": formato, "ruta": str(destino.relative_to(raiz())), "bytes": tam, "revision_base": int(revision_base), "actualizado": timezone.now(),
            "sesion": sesion})
    return b


def borrador_de(plano: Plano, cuenta_id: int) -> BorradorPlano | None:
    b = BorradorPlano.objects.filter(plano=plano, cuenta_id=cuenta_id).first()
    if b is not None and not (raiz() / b.ruta).is_file():
        b.delete()
        return None
    return b


def descartar_borrador(plano: Plano, cuenta_id: int) -> None:
    BorradorPlano.objects.filter(plano=plano, cuenta_id=cuenta_id).delete()
    _borrar_borrador_archivo(plano, cuenta_id)


# -- Papelera --------------------------------------------------------------------------------------------------------------------

def eliminar(plano: Plano, usuario) -> None:
    if not puede_eliminar(usuario, plano):
        raise SinPermiso("Solo quien creó el plano o quien administra las herramientas puede eliminarlo.")
    otro = _bloqueo_de_otro(plano, usuario, "", timezone.now())
    # Si lo tiene abierto la misma cuenta (esta u otra ventana), eliminar es su decisión; si lo edita otra persona, solo quien administra.
    if otro and otro != OTRA_VENTANA and not es_admin(usuario):
        raise ErrorUsuario(f"{otro} está editando este plano. Espera a que termine para eliminarlo.")
    plano.eliminado, plano.eliminado_por_id = timezone.now(), usuario.pk
    plano.eliminado_por_nombre = autoria.de_usuario(usuario)[0]
    plano.bloqueo_cuenta_id, plano.bloqueo_nombre, plano.bloqueo_sesion, plano.bloqueo_latido = None, "", "", None
    plano.save(update_fields=["eliminado", "eliminado_por_id", "eliminado_por_nombre", "bloqueo_cuenta_id", "bloqueo_nombre", "bloqueo_sesion", "bloqueo_latido"])


def restaurar(plano: Plano, usuario) -> None:
    if not (es_admin(usuario) or (puede_editar(usuario) and usuario.pk in (plano.cuenta_id, plano.eliminado_por_id))):
        raise SinPermiso("Solo quien lo eliminó, quien lo creó o quien administra las herramientas puede restaurarlo.")
    plano.eliminado, plano.eliminado_por_id, plano.eliminado_por_nombre = None, None, ""
    plano.save(update_fields=["eliminado", "eliminado_por_id", "eliminado_por_nombre"])


def destruir(plano: Plano) -> None:
    """Borra el plano para siempre: sus versiones, sus borradores y sus archivos."""
    shutil.rmtree(carpeta(plano), ignore_errors=True)
    plano.delete()


def papelera(usuario):
    consulta = Plano.objects.filter(eliminado__isnull=False).order_by("-eliminado")
    if es_admin(usuario):
        return consulta
    from django.db.models import Q

    return consulta.filter(Q(cuenta_id=usuario.pk) | Q(eliminado_por_id=usuario.pk))


def vaciar_papelera() -> int:
    n = 0
    for p in Plano.objects.filter(eliminado__isnull=False):
        destruir(p)
        n += 1
    return n


def vence_en(plano: Plano):
    return plano.eliminado + timedelta(days=PAPELERA_DIAS) if plano.eliminado else None


# -- Mantenimiento (tv_limpiar) y cuentas eliminadas ----------------------------------------------------------------------------

def limpiar() -> int:
    """Diario: planos en la papelera desde hace 30 días, borradores de más de 30 días, salidas de más de un día y temporales."""
    import time

    ahora = timezone.now()
    n = 0
    for p in Plano.objects.filter(eliminado__lt=ahora - timedelta(days=PAPELERA_DIAS)):
        destruir(p)
        n += 1
    for b in BorradorPlano.objects.filter(actualizado__lt=ahora - timedelta(days=BORRADOR_DIAS)).select_related("plano"):
        descartar_borrador(b.plano, b.cuenta_id)
        n += 1
    limite = time.time() - SALIDAS_HORAS * 3600
    for c in (raiz() / "cad" / "salidas", raiz() / "cad" / "tmp"):
        if c.is_dir():
            for archivo in c.iterdir():
                if archivo.is_file() and archivo.stat().st_mtime < limite:
                    archivo.unlink(missing_ok=True)
                    n += 1
    return n


def al_eliminar_cuenta(cuenta_id: int) -> str | None:
    """Contrato 1.8: se sueltan los planos que la persona tenía abiertos para editar y se borran sus borradores de recuperación.

    Los planos (y sus versiones) son de la empresa: se quedan, con la foto de su autor. Idempotente.
    """
    soltados = Plano.objects.filter(bloqueo_cuenta_id=cuenta_id).update(bloqueo_cuenta_id=None, bloqueo_nombre="", bloqueo_sesion="", bloqueo_latido=None)
    borradores = list(BorradorPlano.objects.filter(cuenta_id=cuenta_id).select_related("plano"))
    for b in borradores:
        descartar_borrador(b.plano, cuenta_id)
    partes = []
    if soltados:
        partes.append(f"se liberaron {soltados} plano{'s' if soltados != 1 else ''} que tenía abierto{'s' if soltados != 1 else ''}")
    if borradores:
        partes.append(f"se borraron {len(borradores)} borrador{'es' if len(borradores) != 1 else ''} de recuperación")
    if not partes:
        return None
    texto = "CAD: " + " y ".join(partes) + "."
    return texto[:300]


# -- Consultas para las pantallas ----------------------------------------------------------------------------------------------

def proyectos() -> list[str]:
    """Proyectos y clientes ya escritos en la biblioteca (sugerencias), del más reciente al más viejo, sin repetidos."""
    from ..comun.clientes import clave

    vistos, salida = set(), []
    for nombre in Plano.objects.exclude(proyecto="").order_by("-actualizado").values_list("proyecto", flat=True)[:2000]:
        k = clave(nombre)
        if k and k not in vistos:
            vistos.add(k)
            salida.append(" ".join(nombre.split()))
    return salida[:500]


def sugerencias_proyecto(cuenta_id: int, perfil: str = "grupo_itt") -> list[str]:
    """Los de la biblioteca y, después, los clientes de los reportes y cotizaciones del perfil activo de la persona (cada perfil es un
    espacio aparte: quien solo trabaja en Grupo ITT nunca ve clientes de TresVizo)."""
    from ..comun.clientes import clave, sugerencias

    salida, vistos = [], set()
    for nombre in proyectos() + sugerencias(cuenta_id, perfil=perfil):
        k = clave(nombre)
        if k and k not in vistos:
            vistos.add(k)
            salida.append(nombre)
    return salida[:500]
