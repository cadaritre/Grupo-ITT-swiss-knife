"""Corre todos los casos de `casos.py` en un lado (escritorio o web) y deja sus artefactos en una carpeta.

Se lanza como proceso aparte (lo hace `ejecutor.py` o `golden_windows.py`) con un Python limpio:

    python trabajador.py --lado escritorio --raiz <carpeta con photo_report_app> --entradas <dir> --salida <dir> [--fuentes <dir>]
    python trabajador.py --lado web        --raiz <repositorio web>               --entradas <dir> --salida <dir>

Aísla al escritorio de la máquina: su carpeta de datos (`Documentos\\Grupo ITT App`) queda en un directorio temporal
y nunca toca la del usuario. Con `--fuentes`, el escritorio usa Liberation Sans en lugar de Arial para que los bytes
puedan coincidir en macOS o Linux; sin ella (control en Windows) usa sus fuentes reales.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def _aislar_escritorio(temporal: Path) -> None:
    """Redirige la carpeta Documentos del escritorio a un temporal (HOME/USERPROFILE)."""
    for variable in ("HOME", "USERPROFILE", "TMPDIR", "TMP", "TEMP"):
        os.environ[variable] = str(temporal)
    (temporal / "Documents").mkdir(parents=True, exist_ok=True)


def _importar_sin_api_de_windows(nombre: str):
    """`app_storage` del escritorio pregunta a la API de Windows por la carpeta Documentos al importarse y crea
    ahí sus archivos. Se oculta `ctypes.windll` solo durante esa importación para que use USERPROFILE (temporal)."""
    import ctypes

    guardado = getattr(ctypes, "windll", None)
    if guardado is not None:
        del ctypes.windll
    try:
        return importlib.import_module(nombre)
    finally:
        if guardado is not None:
            ctypes.windll = guardado


def _aplanar(valores: dict, prefijo: str = "") -> dict:
    plano = {}
    for clave, valor in valores.items():
        ruta = f"{prefijo}{clave}"
        if isinstance(valor, dict) and valor:
            plano.update(_aplanar(valor, ruta + "."))
        else:
            plano[ruta] = valor
    return plano


PERSONALES = ("phone", "address", "email", "signer")


def volcar_personales(raiz: Path, salida: Path) -> None:
    """Lee del escritorio sus datos personales y bancarios de fábrica y los deja en un JSON de las entradas.

    Es lo que el perfil corporativo de la web debe suministrar en los casos de paridad. Nunca se guardan en el repositorio:
    se leen del código de `main` en cada corrida.
    """
    temporal = Path(tempfile.mkdtemp(prefix="paridad_personales_"))
    try:
        _aislar_escritorio(temporal)
        sys.path.insert(0, str(raiz))
        _importar_sin_api_de_windows("photo_report_app.app_storage")
        marca = importlib.import_module("photo_report_app.branding")
        modelos = importlib.import_module("photo_report_app.quotation_models")
        generador = importlib.import_module("photo_report_app.pdf_generator")
        cot = modelos.QuoteData()
        opciones = generador.ReportOptions.__dataclass_fields__
        salida.write_text(json.dumps({
            "perfiles": {k: {c: v[c] for c in PERSONALES} for k, v in marca.PROFILE_DEFAULTS.items()},
            "cotizacion": {c: getattr(cot, c) for c in ("bank", "clabe", "account", "prepared_by")},
            "reporte": {c: opciones[c].default for c in ("signer", "professional_license")},
            "firmante_antiguo": marca.LEGACY_ITT_SIGNER,
        }, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def _mezclar(a: dict, b: dict) -> dict:
    r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in a.items()}
    for k, v in b.items():
        r[k] = _mezclar(r[k], v) if isinstance(v, dict) and isinstance(r.get(k), dict) else v
    return r


def _con_personales(valores: dict, personales: dict) -> dict:
    """Los datos personales de fábrica del escritorio, como ajustes de perfil, bajo los ajustes del caso."""
    base = {"branding": {"legacy_signer": personales["firmante_antiguo"], **{k: dict(v) for k, v in personales["perfiles"].items()}}}
    return _mezclar(base, valores)


PERFILES = ("grupo_itt", "tresvizo")


class LadoEscritorio:
    nombre = "escritorio"
    perfil = "grupo_itt"

    def __init__(self, raiz: Path, fuentes: Path | None):
        sys.path.insert(0, str(raiz))
        self.paquete = "photo_report_app"
        self.fuentes = fuentes
        self._storage = _importar_sin_api_de_windows("photo_report_app.app_storage")
        if fuentes is not None:
            self._usar_liberation(fuentes)

    def _usar_liberation(self, fuentes: Path) -> None:
        from PIL import ImageFont
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        pdfmetrics.registerFont(TTFont("GrupoITT-Regular", str(fuentes / "LiberationSans-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("GrupoITT-Bold", str(fuentes / "LiberationSans-Bold.ttf")))
        sketch = self.mod("location_sketch")
        sketch.PDF_REGULAR, sketch.PDF_BOLD = "GrupoITT-Regular", "GrupoITT-Bold"
        cache = {}

        def fuente(size: int, bold: bool = False):
            clave = (size, bold)
            if clave not in cache:
                archivo = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
                cache[clave] = ImageFont.truetype(str(fuentes / archivo), size)
            return cache[clave]

        sketch._font = fuente
        self.mod("maps")._font = fuente

    def mod(self, nombre: str):
        return importlib.import_module(f"{self.paquete}.{nombre}")

    @contextlib.contextmanager
    def ajustes(self, valores: dict):
        valores = _con_personales(_mezclar({"branding": {"profile": self.perfil}}, valores), self.personales)
        ajustes = self._storage.SETTINGS
        previos = {clave: ajustes.get(clave) for clave in valores}
        ajustes.update(_aplanar(valores))
        try:
            yield
        finally:
            ajustes.update(previos)

    def logo(self) -> Path:
        return self.mod("branding").active_profile().logo_path


class LadoWeb:
    nombre = "web"
    perfil = "grupo_itt"

    def __init__(self, raiz: Path, temporal: Path):
        sys.path.insert(0, str(raiz))
        self.paquete = "itt_herramientas.dominio"
        self._almacen = importlib.import_module("itt_herramientas.dominio.almacenamiento")
        self._almacen.configurar_cache(temporal / "cache_web")

    def mod(self, nombre: str):
        return importlib.import_module(f"{self.paquete}.{nombre}")

    def ajustes(self, valores: dict):
        return self._almacen.ajustes_activos(_con_personales(_mezclar({"branding": {"profile": self.perfil}}, valores), self.personales))

    def logo(self) -> Path:
        return self.mod("branding").active_profile().logo_path


def _escribir(salida: Path, artefactos: dict) -> list[str]:
    nombres = []
    for nombre, valor in artefactos.items():
        if isinstance(valor, Path):
            destino = salida / nombre if valor.parent != salida or valor.name != nombre else valor
            if destino != valor:
                shutil.copyfile(valor, destino)
            nombres.append(destino.name)
        else:
            destino = salida / (nombre if nombre.endswith(".json") else nombre + ".json")
            destino.write_text(json.dumps(valor, ensure_ascii=False, indent=1, sort_keys=True, default=str), encoding="utf-8")
            nombres.append(destino.name)
    return sorted(nombres)


def main(argumentos: list[str] | None = None) -> int:
    if argumentos is None and "--volcar-personales" in sys.argv:
        i = sys.argv.index("--volcar-personales")
        volcar_personales(Path(sys.argv[i + 1]), Path(sys.argv[i + 2]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--lado", choices=("escritorio", "web"), required=True)
    ap.add_argument("--raiz", type=Path, required=True)
    ap.add_argument("--entradas", type=Path, required=True)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--fuentes", type=Path, default=None)
    ap.add_argument("--caso", action="append", default=None, help="Solo estos casos")
    args = ap.parse_args(argumentos)

    # Determinismo: ReportLab en modo invariante y sin dependencias del reloj ni de la zona horaria.
    os.environ["RL_invariant"] = "1"
    os.environ["PYTHONHASHSEED"] = "0"
    os.environ["TZ"] = "UTC"
    sys.path.insert(0, str(AQUI))
    temporal = Path(tempfile.mkdtemp(prefix=f"paridad_{args.lado}_"))
    try:
        if args.lado == "escritorio":
            _aislar_escritorio(temporal)
        from reportlab import rl_config
        rl_config.invariant = 1
        lado = LadoEscritorio(args.raiz, args.fuentes) if args.lado == "escritorio" else LadoWeb(args.raiz, temporal)
        import casos

        entradas = {p.name: p for p in args.entradas.iterdir() if p.is_file()}
        # ReportLab nombra las imágenes de un PDF con un resumen de la RUTA del archivo: para que los bytes puedan
        # coincidir, los dos lados leen los logos desde la misma ruta (las entradas comunes).
        personales = json.loads((args.entradas / "personales.json").read_text(encoding="utf-8"))
        lado.personales = personales
        marca = lado.mod("branding")
        marca.ITT_LOGO, marca.TRESVIZO_LOGO = entradas["logo_grupo_itt.png"], entradas["logo_tresvizo.png"]
        args.salida.mkdir(parents=True, exist_ok=True)
        resultados = {}
        for perfil in PERFILES:                       # el escritorio y la web se comparan con cada identidad activa (Grupo ITT y TresVizo)
            lado.perfil = perfil
            for nombre, funcion in casos.CASOS.items():
                if args.caso and nombre not in args.caso:
                    continue
                clave = f"{perfil}/{nombre}"
                carpeta = args.salida / perfil / nombre
                carpeta.mkdir(parents=True, exist_ok=True)
                contexto = casos.Contexto(lado=args.lado, mod=lado.mod, entradas=entradas, salida=carpeta, ajustes=lado.ajustes, logo=lado.logo, personales=personales, perfil=perfil)
                try:
                    with lado.ajustes({}):               # perfil activo de la corrida como base de todos los casos
                        artefactos = funcion(contexto)
                    resultados[clave] = {"artefactos": _escribir(carpeta, artefactos), "error": None}
                except Exception as exc:                     # el error también es un resultado que debe coincidir
                    resultados[clave] = {"artefactos": [], "error": [type(exc).__name__, str(exc)], "traza": traceback.format_exc()}
        (args.salida / "resultados.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        versiones = {}
        for paquete in ("reportlab", "PIL", "ezdxf", "numpy", "shapely", "pyproj", "spellchecker", "wordfreq"):
            with contextlib.suppress(Exception):
                modulo = importlib.import_module(paquete)
                versiones[paquete] = getattr(modulo, "__version__", None) or getattr(modulo, "VERSION", None)
        import platform
        (args.salida / "entorno.json").write_text(json.dumps({"lado": args.lado, "python": platform.python_version(), "sistema": platform.platform(),
                                                               "fuentes_liberation": args.fuentes is not None, "versiones": {k: str(v) for k, v in versiones.items()}},
                                                              indent=1, sort_keys=True), encoding="utf-8")
        fallidos = [n for n, r in resultados.items() if r["error"]]
        for n in fallidos:
            print(f"[{args.lado}] {n}: {resultados[n]['error']}", file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
