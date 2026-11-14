"""Envío a Google Drive (herramientas-web.md §3): conexión por cuenta, jerarquía y cola de pendientes con reintentos.

Mismo contrato que el escritorio (`cloud_sync.py`): `GRUPO ITT REPORTES/<persona>/{Cotizaciones, Reportes fotograficos, Poligonos KML}`, solo estos
tipos de archivo (DXF, croquis y triangulaciones no se envían) y nombres que no pisan un archivo distinto: si ya existe el mismo contenido no se
vuelve a subir; si existe otro con ese nombre, se agrega una marca de fecha. Con el permiso `drive.file` la app solo ve lo que ella creó o lo que la
persona le abrió con el selector de Google, por eso la carpeta compartida se elige con Picker y de ahí en adelante todo cuelga de ella.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.utils import timezone

from ..comun import capturas
from ..models import ConexionDrive, EnvioDrive
from . import cifrado, google

CARPETA_RAIZ = "GRUPO ITT REPORTES"
CATEGORIAS = {"cotizaciones": ("Cotizaciones", {".pdf", ".json"}), "reportes": ("Reportes fotograficos", {".pdf"}), "poligonos": ("Poligonos KML", {".kml", ".kmz"})}
TIPOS = {".pdf": "application/pdf", ".json": "application/json", ".kml": "application/vnd.google-earth.kml+xml", ".kmz": "application/vnd.google-earth.kmz"}
MAX_INTENTOS = 12
LOTE = 20
VIDA_ENVIADOS_DIAS = 30
_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}

#: Transporte de Google para las pruebas (None = internet real).
_transporte = None


def cliente() -> google.Google:
    return google.Google(_transporte)


def disponible() -> bool:
    """Hay cliente OAuth configurado en el contrato (`TV_DRIVE_CLIENT_ID` y `TV_DRIVE_CLIENT_SECRET`); vacíos = sin Drive."""
    return cliente().configurado()


def nombre_carpeta(valor: str, respaldo: str = "Usuario sin nombre") -> str:
    """Igual que el escritorio: sin caracteres que Windows no admite, sin puntos ni espacios al final ni nombres reservados."""
    limpio = re.sub(r"\s+", " ", _INVALIDO.sub(" ", str(valor or ""))).strip(" .")
    if not limpio or limpio.upper() in _RESERVADOS:
        return respaldo
    return limpio[:100].strip(" .") or respaldo


def conexion_de(cuenta_id: int) -> ConexionDrive | None:
    return ConexionDrive.objects.filter(cuenta_id=cuenta_id).first()


# -- Conexión -------------------------------------------------------------------------------------------------------------------

def nombre_de_persona(cuenta_id: int) -> str:
    u = get_user_model().objects.filter(pk=cuenta_id).first()
    return nombre_carpeta((u.get_full_name() or u.get_username()) if u else "")


def conectar(cuenta_id: int, tokens: dict) -> ConexionDrive:
    """Guarda la conexión con los tokens que devolvió Google (el de actualización, cifrado). Conserva la carpeta si ya había una."""
    actualizacion = tokens.get("refresh_token")
    previa = conexion_de(cuenta_id)
    if not actualizacion and previa is None:
        raise google.Reautorizar("Google no entregó un token de actualización. Quita el acceso de la app en tu cuenta de Google y vuelve a conectar.")
    g = cliente()
    correo = g.correo(tokens["access_token"])
    c = previa or ConexionDrive(cuenta_id=cuenta_id, persona=nombre_de_persona(cuenta_id))
    if actualizacion:
        c.token_cifrado = cifrado.cifrar(actualizacion)
    c.correo, c.estado, c.error, c.actualizada = correo or c.correo, ConexionDrive.Estado.OK, "", timezone.now()
    c.save()
    _guardar_acceso(c, tokens)
    return c


def desconectar(c: ConexionDrive) -> None:
    """Revoca el acceso en Google y olvida el token. Lo pendiente se descarta (no hay a dónde enviarlo); lo ya enviado queda en Drive."""
    try:
        cliente().revocar(cifrado.descifrar(c.token_cifrado))
    except cifrado.TokenIlegible:
        pass
    pendientes = EnvioDrive.objects.filter(cuenta_id=c.cuenta_id, estado__in=[EnvioDrive.Estado.PENDIENTE, EnvioDrive.Estado.ENVIANDO, EnvioDrive.Estado.ERROR])
    for e in pendientes:
        _borrar_copia(e)
    pendientes.update(estado=EnvioDrive.Estado.DESCARTADO, error="Drive se desconectó.")
    cache.delete(f"drive:acceso:{c.cuenta_id}")
    c.delete()


def _guardar_acceso(c: ConexionDrive, tokens: dict) -> None:
    cache.set(f"drive:acceso:{c.cuenta_id}", tokens["access_token"], max(60, int(tokens.get("expires_in", 3600)) - 120))


def acceso(c: ConexionDrive, forzar: bool = False) -> str:
    """Token de acceso vigente (guardado en caché unos minutos). Si Google rechaza el de actualización, la conexión queda «por reconectar»."""
    clave = f"drive:acceso:{c.cuenta_id}"
    if not forzar and (t := cache.get(clave)):
        return t
    try:
        tokens = cliente().renovar(cifrado.descifrar(c.token_cifrado))
    except (google.Reautorizar, cifrado.TokenIlegible) as exc:
        c.estado, c.error = ConexionDrive.Estado.REAUTORIZAR, "Google ya no acepta esta conexión. Vuelve a conectar Drive."
        c.save(update_fields=["estado", "error"])
        raise google.Reautorizar("Vuelve a conectar Drive.") from exc
    _guardar_acceso(c, tokens)
    if c.estado == ConexionDrive.Estado.REAUTORIZAR:
        c.estado, c.error = ConexionDrive.Estado.OK, ""
        c.save(update_fields=["estado", "error"])
    return tokens["access_token"]


def con_acceso(c: ConexionDrive, trabajo):
    """Corre `trabajo(token)`; si el token caducó a media operación, lo renueva y repite una vez."""
    try:
        return trabajo(acceso(c))
    except google.TokenVencido:
        return trabajo(acceso(c, forzar=True))


# -- Jerarquía ------------------------------------------------------------------------------------------------------------------

def _es_carpeta(datos: dict) -> bool:
    return datos.get("mimeType") == google.CARPETA


def _buscar_o_crear(token: str, nombre: str, padre: str) -> str:
    g = cliente()
    for f in g.buscar(token, padre, nombre, carpeta=True):
        return f["id"]
    return g.crear_carpeta(token, nombre, padre)["id"]


def asegurar_jerarquia(c: ConexionDrive) -> dict:
    """Garantiza `<raíz>/<persona>/<3 categorías>` y guarda sus ids. Si la raíz ya no existe, la conexión queda con «carpeta perdida»."""
    if not c.carpeta_raiz_id:
        raise google.NoDisponible("Todavía no eliges la carpeta de Drive donde se guardan los archivos.", 404, "sin_carpeta")

    def trabajo(token):
        try:
            if not _es_carpeta(cliente().archivo(token, c.carpeta_raiz_id)):
                raise google.NoDisponible("Lo elegido no es una carpeta.", 404, "no_carpeta")
        except google.NoDisponible:
            c.estado, c.error = ConexionDrive.Estado.CARPETA_PERDIDA, "La carpeta de Drive ya no está disponible (se borró o se quitó el acceso). Elígela de nuevo."
            c.save(update_fields=["estado", "error"])
            raise
        persona = _buscar_o_crear(token, nombre_carpeta(c.persona), c.carpeta_raiz_id)
        ids = {"persona": persona}
        for clave, (carpeta, _) in CATEGORIAS.items():
            ids[clave] = _buscar_o_crear(token, carpeta, persona)
        return ids

    ids = con_acceso(c, trabajo)
    c.carpetas, c.estado, c.error, c.actualizada = ids, ConexionDrive.Estado.OK, "", timezone.now()
    c.save(update_fields=["carpetas", "estado", "error", "actualizada"])
    return ids


def elegir_carpeta(c: ConexionDrive, carpeta_id: str) -> ConexionDrive:
    """La persona eligió `carpeta_id` con Picker: se comprueba que la app la ve y que es una carpeta, y se arma la jerarquía dentro."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", carpeta_id or ""):
        raise google.ErrorGoogle("El identificador de la carpeta no es válido.")
    datos = con_acceso(c, lambda token: cliente().archivo(token, carpeta_id))
    if not _es_carpeta(datos):
        raise google.ErrorGoogle("Lo que elegiste no es una carpeta.")
    c.carpeta_raiz_id, c.carpetas = carpeta_id, {}
    c.save(update_fields=["carpeta_raiz_id", "carpetas"])
    asegurar_jerarquia(c)
    return c


def crear_raiz_propia(c: ConexionDrive) -> ConexionDrive:
    """Sin carpeta compartida: la app crea «GRUPO ITT REPORTES» en el Drive de la persona (la ve porque ella misma la creó)."""
    carpeta = con_acceso(c, lambda token: cliente().crear_carpeta(token, CARPETA_RAIZ))
    c.carpeta_raiz_id, c.carpetas = carpeta["id"], {}
    c.save(update_fields=["carpeta_raiz_id", "carpetas"])
    asegurar_jerarquia(c)
    return c


def cambiar_persona(c: ConexionDrive, nombre: str) -> None:
    nuevo = nombre_carpeta(nombre)
    if nuevo != c.persona:
        c.persona, c.carpetas = nuevo, {}
        c.save(update_fields=["persona", "carpetas"])


# -- Cola de envío -------------------------------------------------------------------------------------------------------------------

def _carpeta_cola(cuenta_id: int) -> Path:
    return capturas.raiz() / "drive_cola" / str(cuenta_id)


def _borrar_copia(e: EnvioDrive) -> None:
    ruta = capturas.raiz() / e.ruta
    if e.ruta and ruta.is_file():
        ruta.unlink(missing_ok=True)
        try:
            ruta.parent.rmdir()
        except OSError:
            pass


def elegible(categoria: str, nombre: str) -> bool:
    return categoria in CATEGORIAS and Path(nombre).suffix.lower() in CATEGORIAS[categoria][1]


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def clave_de(*partes) -> str:
    """Huella de lo que originó el archivo (datos, revisión, opciones). Un PDF regenerado sin cambios tiene otros bytes (fechas internas) pero la misma clave."""
    return hashlib.sha256("\x1f".join(str(p) for p in partes).encode("utf-8")).hexdigest()


def encolar(cuenta_id: int, categoria: str, nombre: str, origen: Path | None = None, contenido: bytes | None = None, forzar: bool = False, clave: str = "") -> EnvioDrive | None:
    """Pone un archivo en la cola de la cuenta si tiene Drive conectado (y envío automático, salvo `forzar`). Nunca falla el flujo que lo llama.

    Guarda una COPIA: el original puede cambiar o borrarse. El mismo contenido con el mismo nombre no se encola dos veces.
    """
    try:
        c = conexion_de(cuenta_id)
        if c is None or not disponible() or not elegible(categoria, nombre) or not (c.automatico or forzar):
            return None
        nombre = nombre_carpeta(nombre, "archivo")[:200]
        if clave:
            igual = EnvioDrive.objects.filter(cuenta_id=cuenta_id, categoria=categoria, nombre=nombre, clave=clave,
                                              estado__in=[EnvioDrive.Estado.PENDIENTE, EnvioDrive.Estado.ENVIANDO, EnvioDrive.Estado.ENVIADO]).first()
            if igual is not None:
                return igual
        carpeta = _carpeta_cola(cuenta_id) / uuid.uuid4().hex
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = carpeta / nombre
        if contenido is not None:
            destino.write_bytes(contenido)
        else:
            shutil.copy2(origen, destino)
        sha = _sha256(destino)
        previo = EnvioDrive.objects.filter(cuenta_id=cuenta_id, categoria=categoria, nombre=nombre, sha256=sha).first()
        if previo is not None:
            shutil.rmtree(carpeta, ignore_errors=True)
            if previo.estado in (EnvioDrive.Estado.DESCARTADO, EnvioDrive.Estado.ERROR):
                previo.estado, previo.intentos, previo.error, previo.proximo_intento = EnvioDrive.Estado.PENDIENTE, 0, "", timezone.now()
                previo.save()
                _recopiar(previo, contenido, origen)
                return _disparar(previo)
            return previo
        e = EnvioDrive.objects.create(cuenta_id=cuenta_id, categoria=categoria, nombre=nombre, ruta=destino.relative_to(capturas.raiz()).as_posix(), sha256=sha, clave=clave, bytes=destino.stat().st_size)
        return _disparar(e)
    except (OSError, IntegrityError):
        return None


def _recopiar(e: EnvioDrive, contenido: bytes | None, origen: Path | None) -> bool:
    carpeta = _carpeta_cola(e.cuenta_id) / uuid.uuid4().hex
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / e.nombre
    if contenido is not None:
        destino.write_bytes(contenido)
    else:
        shutil.copy2(origen, destino)
    e.ruta = destino.relative_to(capturas.raiz()).as_posix()
    e.save(update_fields=["ruta"])
    return True


def _disparar(e: EnvioDrive) -> EnvioDrive:
    from ..tareas import enviar_a_drive

    enviar_a_drive.encolar(e.cuenta_id)
    return e


def _md5(ruta: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _nombre_libre(token: str, carpeta_id: str, nombre: str, md5: str) -> tuple[str, dict | None]:
    """(nombre a usar, archivo igual que ya está). Mismo contenido: no se sube otra vez. Otro contenido con ese nombre: marca de fecha."""
    g = cliente()
    ya = g.buscar(token, carpeta_id, nombre, carpeta=False)
    if not ya:
        return nombre, None
    for f in ya:
        if f.get("md5Checksum") == md5:
            return nombre, f
    base, sufijo = Path(nombre).stem, Path(nombre).suffix
    marca = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    contador = 1
    while True:
        candidato = f"{base}_{marca}{'' if contador == 1 else f'_{contador}'}{sufijo}"
        existentes = g.buscar(token, carpeta_id, candidato, carpeta=False)
        if not existentes:
            return candidato, None
        for f in existentes:
            if f.get("md5Checksum") == md5:
                return candidato, f
        contador += 1


def _espera(intentos: int) -> timedelta:
    return timedelta(seconds=min(60 * 2 ** max(0, intentos - 1), 6 * 3600))


def enviar_uno(e: EnvioDrive, c: ConexionDrive) -> bool:
    """Intenta enviar un archivo. True si quedó en Drive. Los errores pasajeros reprograman; los permanentes lo dejan «con error» para reintentar a mano."""
    ruta = capturas.raiz() / e.ruta
    e.estado, e.intentos = EnvioDrive.Estado.ENVIANDO, e.intentos + 1
    e.save(update_fields=["estado", "intentos"])
    try:
        if not ruta.is_file():
            raise google.ErrorGoogle("La copia local del archivo ya no existe.")
        if not c.carpetas.get(e.categoria):
            asegurar_jerarquia(c)
        md5 = _md5(ruta)

        def trabajo(token):
            carpeta_id = c.carpetas[e.categoria]
            try:
                cliente().archivo(token, carpeta_id)                                 # ¿sigue ahí (y fuera de la papelera)? si no, se rehace la jerarquía
                nombre, igual = _nombre_libre(token, carpeta_id, e.nombre, md5)
            except google.NoDisponible:                                              # la carpeta de la categoría ya no está: se rehace la jerarquía
                asegurar_jerarquia(c)
                carpeta_id = c.carpetas[e.categoria]
                nombre, igual = _nombre_libre(token, carpeta_id, e.nombre, md5)
            if igual is not None:
                return igual, nombre
            subido = cliente().subir(token, ruta, nombre, carpeta_id, TIPOS.get(ruta.suffix.lower(), "application/octet-stream"))
            if subido.get("md5Checksum") and subido["md5Checksum"] != md5:
                raise google.Temporal("Drive guardó un contenido distinto del enviado; se reintenta.")
            return subido, nombre

        resultado, nombre = con_acceso(c, trabajo)
    except google.Reautorizar as exc:
        _reprogramar(e, str(exc) or "Vuelve a conectar Drive.", horas=1, contar=False)
        return False
    except google.NoDisponible as exc:
        if c.estado != ConexionDrive.Estado.CARPETA_PERDIDA:
            c.estado, c.error = ConexionDrive.Estado.CARPETA_PERDIDA, "La carpeta de Drive ya no está disponible. Elígela de nuevo."
            c.save(update_fields=["estado", "error"])
        _reprogramar(e, str(exc), horas=1, contar=False)
        return False
    except google.Temporal as exc:
        _reprogramar(e, str(exc))
        return False
    except google.ErrorGoogle as exc:
        e.estado, e.error = (EnvioDrive.Estado.ERROR if e.intentos >= MAX_INTENTOS else EnvioDrive.Estado.PENDIENTE), str(exc)[:300]
        e.proximo_intento = timezone.now() + _espera(e.intentos)
        e.save(update_fields=["estado", "error", "proximo_intento"])
        return False
    e.estado, e.error, e.drive_id, e.drive_nombre, e.enviada = EnvioDrive.Estado.ENVIADO, "", resultado.get("id", ""), nombre, timezone.now()
    e.save(update_fields=["estado", "error", "drive_id", "drive_nombre", "enviada"])
    _borrar_copia(e)
    return True


def _reprogramar(e: EnvioDrive, mensaje: str, horas: float | None = None, contar: bool = True) -> None:
    if not contar:
        e.intentos = max(0, e.intentos - 1)
    e.estado = EnvioDrive.Estado.ERROR if e.intentos >= MAX_INTENTOS else EnvioDrive.Estado.PENDIENTE
    e.error = mensaje[:300]
    e.proximo_intento = timezone.now() + (timedelta(hours=horas) if horas else _espera(e.intentos))
    e.save(update_fields=["estado", "error", "proximo_intento", "intentos"])


def procesar(cuenta_id: int, ahora=None, forzar: bool = False) -> dict:
    """Envía lo pendiente de la cuenta cuyo turno ya llegó (o todo, con `forzar`). Devuelve {enviados, pendientes}."""
    c = conexion_de(cuenta_id)
    resultado = {"enviados": 0, "pendientes": 0}
    if c is None or not disponible():
        return resultado
    ahora = ahora or timezone.now()
    consulta = EnvioDrive.objects.filter(cuenta_id=cuenta_id, estado__in=[EnvioDrive.Estado.PENDIENTE, EnvioDrive.Estado.ENVIANDO])
    if not forzar:
        consulta = consulta.filter(proximo_intento__lte=ahora)
    for e in consulta.order_by("creada")[:LOTE]:
        if c.estado == ConexionDrive.Estado.REAUTORIZAR and not forzar:
            break
        if enviar_uno(e, c):
            resultado["enviados"] += 1
        c.refresh_from_db()
    resultado["pendientes"] = EnvioDrive.objects.filter(cuenta_id=cuenta_id, estado=EnvioDrive.Estado.PENDIENTE).count()
    return resultado


def reintentar_todo() -> int:
    """Para un temporizador (`manage.py herramientas_drive_reintentar`): procesa las cuentas con envíos pendientes que ya tocan."""
    cuentas = EnvioDrive.objects.filter(estado=EnvioDrive.Estado.PENDIENTE, proximo_intento__lte=timezone.now()).values_list("cuenta_id", flat=True).distinct()
    return sum(procesar(c)["enviados"] for c in list(cuentas))


def reintentar_envio(e: EnvioDrive) -> None:
    e.estado, e.intentos, e.error, e.proximo_intento = EnvioDrive.Estado.PENDIENTE, 0, "", timezone.now()
    e.save(update_fields=["estado", "intentos", "error", "proximo_intento"])


def descartar_envio(e: EnvioDrive) -> None:
    _borrar_copia(e)
    e.estado, e.error = EnvioDrive.Estado.DESCARTADO, "Descartado por la persona."
    e.save(update_fields=["estado", "error"])


def limpiar() -> int:
    """`tv_limpiar` (diario): olvida los envíos ya terminados de hace más de 30 días y reintenta lo pendiente."""
    viejos = EnvioDrive.objects.filter(estado__in=[EnvioDrive.Estado.ENVIADO, EnvioDrive.Estado.DESCARTADO], creada__lt=timezone.now() - timedelta(days=VIDA_ENVIADOS_DIAS))
    n = viejos.count()
    viejos.delete()
    try:
        reintentar_todo()
    except Exception:                                                              # una cuenta con problemas no debe detener la limpieza del resto
        pass
    return n
