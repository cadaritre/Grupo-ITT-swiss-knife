"""Motor nativo de Open CAD Studio sin ventana (`OpenCADStudio --serve`): imprimir a PDF y convertir entre DWG y DXF en el servidor.

La compilación web no imprime a PDF; la nativa de Linux sí, sin pantalla ni GPU (ensayado en la VM: un plano de 50 000 entidades con
textos usa ~2.4 GB; de líneas y polilíneas, 0.4–0.6 GB). Cada trabajo abre un proceso nuevo y lo cierra al terminar: la memoria se
libera completa y un plano que no cabe solo mata a ese proceso (el trabajador de la cola `cad` tiene su propio tope de memoria).

Protocolo: una petición JSON por línea en la entrada estándar, una respuesta por línea en la salida (docs/automation/native.md).
"""
from __future__ import annotations

import json
import logging
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from django.conf import settings

from ..comun.errores import ErrorUsuario

log = logging.getLogger("itt_herramientas.cad")

#: Papeles del catálogo del motor (nombre canónico) -> etiqueta. Medidas en mm.
PAPELES = {
    "Letter_(8.50_x_11.00_Inches)": "Carta (216 × 279 mm)",
    "Legal_(8.50_x_14.00_Inches)": "Oficio (216 × 356 mm)",
    "Tabloid_(11.00_x_17.00_Inches)": "Doble carta (279 × 432 mm)",
    "ISO_A4_(210.00_x_297.00_MM)": "A4 (210 × 297 mm)",
    "ISO_A3_(297.00_x_420.00_MM)": "A3 (297 × 420 mm)",
    "ISO_A2_(420.00_x_594.00_MM)": "A2 (420 × 594 mm)",
    "ISO_A1_(594.00_x_841.00_MM)": "A1 (594 × 841 mm)",
    "ISO_A0_(841.00_x_1189.00_MM)": "A0 (841 × 1189 mm)",
}
PAPEL_POR_OMISION = "Letter_(8.50_x_11.00_Inches)"
ORIENTACIONES = {"landscape": "Horizontal", "portrait": "Vertical"}
AREAS = {"modelo": "Espacio modelo (todo el dibujo)", "presentaciones": "Presentaciones (una página por cada una)"}
ESCALAS = ("ajustar", "1:1", "1:50", "1:100", "1:200", "1:250", "1:500", "1:1000", "1:2000", "1:2500", "1:5000", "1:10000")
#: Milímetros por unidad de dibujo según $INSUNITS. El motor entiende «1:100» como «1 mm de papel = 100 unidades de dibujo» (convención de
#: AutoCAD) y no mira las unidades: en un plano en metros «1:1000» salía a 1:1 000 000 (medido, 15-11-2026). Sin unidades (0), metros:
#: los planos de la empresa son topográficos en coordenadas UTM.
MM_POR_UNIDAD = {0: 1000.0, 1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1_000_000.0, 8: 0.0000254, 9: 0.0254,
                 10: 914.4, 14: 100.0, 15: 10_000.0, 16: 100_000.0}
TIEMPO_ARRANQUE = 30
TIEMPO_ABRIR = 120
TIEMPO_TRABAJO = 300
#: Tope de cualquier archivo que escriba el motor (el PDF, sus temporales): un plano que se desborda falla, no llena el disco.
TOPE_ARCHIVO = 2 * 1024 ** 3
#: Margen del trabajador dentro del MemoryMax de tv-tareas-cad: el motor tiene como tope de datos (RLIMIT_DATA) ese MemoryMax menos esto.
#: Un plano que no cabe hace fallar la petición de memoria del propio motor (aborta con «memory allocation of … failed») en vez de llevar al
#: grupo al tope, donde el núcleo mató una vez al trabajador junto con el motor (ensayo de L, 15-11-2026). Medido: el plano más pesado que sí
#: cabe usa 1.15 GB de datos.
MARGEN_TRABAJADOR_MB = 256


def tope_datos() -> int:
    return (int(getattr(settings, "HERRAMIENTAS_CAD_MEMORIA_MB", 2048) or 2048) - MARGEN_TRABAJADOR_MB) * 1024 ** 2


class MotorNoDisponible(ErrorUsuario):
    pass


class DemasiadoGrande(ErrorUsuario):
    pass


class Perdida(ErrorUsuario):
    """Convertir dejaría fuera elementos que el otro formato no admite: hay que confirmarlo (`allow_lossy`)."""


def _primero_en_caer() -> None:
    """Si falta memoria, el núcleo mata primero al motor y no al trabajador ni a otro servicio (Linux; subir el valor no pide permisos).

    Lo destapó el ensayo con un plano de 50 000 textos en la VM: sin esto el núcleo mataba a los trabajadores de Python (mismo ajuste que el
    motor) y el motor seguía hasta agotar su tiempo.
    """
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass
    # Defensa en profundidad (el motor interpreta planos ajenos): archivos de 2 GB como máximo y sin volcados de memoria.
    import resource

    _tope = tope_datos()
    for limite, valor in ((resource.RLIMIT_FSIZE, TOPE_ARCHIVO), (resource.RLIMIT_CORE, 0), (resource.RLIMIT_DATA, _tope)):
        try:
            _, duro = resource.getrlimit(limite)
            resource.setrlimit(limite, (valor if duro == resource.RLIM_INFINITY else min(valor, duro),) * 2)
        except (ValueError, OSError):
            pass


def _poner_fuentes(carpeta: Path) -> None:
    """Las fuentes SHX de la empresa (CAD → Fuentes) en la carpeta de fuentes del motor: `<XDG_CONFIG_HOME>/OpenCADStudio/fonts`.

    El motor busca la .shx de un estilo junto al plano y después ahí (por nombre, sin importar mayúsculas). Los planos de la biblioteca se
    guardan con otro nombre y sin sus fuentes, así que sin esto el PDF sale con la letra sustituta aunque el editor muestre la SHX real.
    Un enlace por fuente con el nombre que usan los estilos («txt.shx»); si algo falla, se imprime igual con la sustituta.
    """
    try:
        from ..models import FuenteCad
        from . import fuentes

        carpeta.mkdir(parents=True, exist_ok=True)
        for f in FuenteCad.objects.all():
            origen = fuentes.ruta(f)
            if origen.is_file():
                (carpeta / f.nombre).symlink_to(origen)
    except Exception:  # noqa: BLE001
        log.warning("CAD nativo: no se pudieron poner las fuentes SHX para el motor", exc_info=True)


def binario() -> Path | None:
    ruta = getattr(settings, "HERRAMIENTAS_CAD_NATIVO", "") or ""
    if not ruta:
        return None
    p = Path(ruta)
    return p if p.is_file() and os.access(p, os.X_OK) else None


def disponible() -> bool:
    return binario() is not None


def escala_valida(texto: str) -> str:
    texto = (texto or "").strip().lower().replace(" ", "")
    if texto in ("", "ajustar"):
        return "ajustar"
    partes = texto.split(":")
    try:
        a, b = (float(x) for x in partes)
    except ValueError:
        raise ErrorUsuario("Escribe la escala como 1:100.") from None
    if len(partes) != 2 or not (0 < a <= 1e6 and 0 < b <= 1e7):
        raise ErrorUsuario("Escribe la escala como 1:100.")
    return f"{a:g}:{b:g}"


def escala_del_motor(escala: str, unidades) -> str:
    """«1:100» real (papel:terreno) -> la escala del motor (mm de papel : unidades de dibujo), según las unidades del plano."""
    mm = MM_POR_UNIDAD.get(unidades if isinstance(unidades, int) else 0, 1000.0)
    a, b = (float(x) for x in escala.split(":"))
    return f"{a * mm:g}:{b:g}"


class Sesion:
    """Un proceso `--serve` con su propia carpeta de usuario temporal. Úsalo con `with`."""

    def __init__(self):
        b = binario()
        if b is None:
            raise MotorNoDisponible("La impresión y la conversión en el servidor no están disponibles en este momento.")
        self._proc = None
        self._hogar = Path(tempfile.mkdtemp(prefix="ocs-"))
        _poner_fuentes(self._hogar / "config" / "OpenCADStudio" / "fonts")
        try:
            # Entorno mínimo: nada del trabajador (ni sus credenciales ni las variables de la base) llega al motor.
            entorno = {"HOME": str(self._hogar), "XDG_CONFIG_HOME": str(self._hogar / "config"), "XDG_CACHE_HOME": str(self._hogar / "cache"),
                       "XDG_DATA_HOME": str(self._hogar / "data"), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": str(self._hogar)}
            # Los avisos del motor van a un archivo, no a una tubería que nadie lee: con más de 64 kB el motor se quedaría esperando.
            self._avisos = self._hogar / "motor.log"
            with open(self._avisos, "wb") as avisos:
                self._proc = subprocess.Popen([str(b), "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=avisos,
                                              env=entorno, cwd=self._hogar, start_new_session=True, close_fds=True, preexec_fn=_primero_en_caer)
            self._resto = b""
            self._n = 0
            banner = self._leer(TIEMPO_ARRANQUE)
            if not banner.get("ready"):
                raise MotorNoDisponible("El motor de impresión no arrancó. Inténtalo de nuevo en unos minutos.")
        except BaseException:
            self.cerrar()
            raise
        self.version = banner.get("version", "")
        self.documento = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()

    def _leer(self, tiempo: float) -> dict:
        fin = time.monotonic() + tiempo
        while b"\n" not in self._resto:
            restante = fin - time.monotonic()
            if restante <= 0:
                self._matar()
                raise ErrorUsuario("El plano tardó demasiado en procesarse. Inténtalo con una sola presentación o un papel más chico.")
            listo, _, _ = select.select([self._proc.stdout], [], [], min(restante, 1.0))
            if listo:
                trozo = os.read(self._proc.stdout.fileno(), 65536)
                if not trozo:
                    self._terminado()
                self._resto += trozo
            elif self._proc.poll() is not None:
                self._terminado()
        linea, self._resto = self._resto.split(b"\n", 1)
        try:
            return json.loads(linea)
        except ValueError:
            log.error("CAD nativo: respuesta ilegible: %r", linea[:300])
            raise ErrorUsuario("El motor de impresión respondió algo inesperado. Inténtalo de nuevo.") from None

    def _primer_aviso(self) -> str:
        """La primera línea de los avisos del motor, corta: el registro no guarda textos del plano (pueden traer datos de clientes)."""
        try:
            with open(self._avisos, "rb") as f:
                return f.readline(200).decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def _sin_memoria(self) -> bool:
        """El motor abortó porque no le dieron memoria (llegó a tope_datos()): Rust lo dice en sus avisos antes de abortar."""
        try:
            with open(self._avisos, "rb") as f:
                f.seek(max(0, f.seek(0, 2) - 8192))
                return b"memory allocation of" in f.read()
        except OSError:
            return False

    def _terminado(self):
        try:
            codigo = self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # cerró su salida pero sigue vivo: se detiene y se dice como cualquier otra caída
            self._matar()
            log.error("CAD nativo cerró su salida sin terminar (%s)", self._primer_aviso())
            raise ErrorUsuario("El motor de impresión se detuvo de forma inesperada. Inténtalo de nuevo.") from None
        log.error("CAD nativo terminó con código %s (%s)", codigo, self._primer_aviso())
        if codigo in (-signal.SIGKILL, 137) or (codigo in (-signal.SIGABRT, 134) and self._sin_memoria()):
            raise DemasiadoGrande("El plano es demasiado grande para procesarlo en el servidor (se acabó la memoria). Imprime una parte o "
                                  "ábrelo en el programa de escritorio.")
        raise ErrorUsuario("El motor de impresión se detuvo de forma inesperada. Inténtalo de nuevo.")

    def pedir(self, op: str, tiempo: float = TIEMPO_TRABAJO, **campos) -> dict:
        self._n += 1
        peticion = {"protocol": 1, "request_id": f"tv-{uuid.uuid4().hex[:12]}-{self._n}", "op": op, **campos}
        if self.documento is not None and "document_id" not in peticion and op != "open":
            peticion["document_id"] = self.documento
        try:
            self._proc.stdin.write((json.dumps(peticion) + "\n").encode())
            self._proc.stdin.flush()
        except BrokenPipeError:
            self._terminado()
        return self._leer(tiempo)

    def abrir(self, ruta: Path) -> None:
        r = self.pedir("open", TIEMPO_ABRIR, path=str(ruta))
        if not r.get("ok"):
            log.warning("CAD nativo: no abrió %s: %s", ruta.name, r.get("code") or r.get("error"))
            raise ErrorUsuario("No se pudo abrir el plano para procesarlo. Revisa que no esté dañado.")
        self.documento = (r.get("state") or {}).get("document_id")

    def cerrar(self):
        if getattr(self, "_proc", None) is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except Exception:
                self._matar()
        shutil.rmtree(getattr(self, "_hogar", ""), ignore_errors=True)

    def _matar(self):
        try:
            os.killpg(self._proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass


def imprimir(entrada: Path, salida: Path, papel: str, orientacion: str, escala: str, area: str, grosores: bool = True) -> dict:
    """PDF del plano. `escala`: «ajustar» o «1:100»; `area`: «modelo» o «presentaciones». Devuelve {paginas, tamanos}."""
    if papel not in PAPELES:
        raise ErrorUsuario("Elige un tamaño de papel de la lista.")
    if orientacion not in ORIENTACIONES:
        raise ErrorUsuario("Elige la orientación: horizontal o vertical.")
    if area not in AREAS:
        raise ErrorUsuario("Elige qué imprimir: el espacio modelo o las presentaciones.")
    escala = escala_valida(escala)
    campos = {"path": str(salida), "paper": papel, "orientation": orientacion, "lineweights": grosores, "center": True}
    if area == "modelo":
        campos.update(layout="Model", area="extents")
    else:
        campos.update(layout="all", area="layout")
    if escala == "ajustar":
        campos["fit"] = True
    with Sesion() as s:
        s.abrir(entrada)
        if escala != "ajustar":   # las presentaciones ya están en mm de papel; el espacio modelo, en las unidades del plano
            campos["scale"] = escala if area != "modelo" else escala_del_motor(escala, (s.pedir("header", TIEMPO_ABRIR) or {}).get("insertion_units"))
        r = s.pedir("plot", **campos)
    if not r.get("ok"):
        codigo = r.get("code") or ""
        log.warning("CAD nativo: plot falló: %s %s", codigo, r.get("error"))
        if codigo == "layout_missing" or "layout" in codigo:
            raise ErrorUsuario("Este plano no tiene presentaciones. Imprime el espacio modelo.")
        raise ErrorUsuario("No se pudo imprimir el plano con estas opciones. Prueba con «Ajustar al papel».")
    if not salida.is_file():
        raise ErrorUsuario("No se pudo imprimir el plano. Inténtalo de nuevo.")
    res = r.get("result") or {}
    return {"paginas": int(res.get("pages") or 1), "tamanos": res.get("page_sizes") or []}


def convertir(entrada: Path, salida: Path, formato: str, permitir_perdida: bool = False) -> dict:
    """Guarda el plano en el otro formato con la versión del propio dibujo. Si se perdería algo, lanza `Perdida` (a menos que se permita)."""
    if formato not in ("dwg", "dxf"):
        raise ErrorUsuario("Elige DWG o DXF.")
    with Sesion() as s:
        s.abrir(entrada)
        campos = {"path": str(salida), "target_format": formato}
        if permitir_perdida:
            campos["allow_lossy"] = True
        r = s.pedir("save", **campos)
    if not r.get("ok"):
        if r.get("code") == "lossy_conversion_not_acknowledged":
            raise Perdida(f"Al guardar en {formato.upper()} se perderían elementos que ese formato no admite.")
        log.warning("CAD nativo: conversión falló: %s %s", r.get("code"), r.get("error"))
        raise ErrorUsuario(f"No se pudo convertir el plano a {formato.upper()}.")
    res = r.get("result") or {}
    return {"perdidos": int(res.get("dropped_on_save") or 0), "version": res.get("target_version", "")}
