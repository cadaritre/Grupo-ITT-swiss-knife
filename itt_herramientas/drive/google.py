"""Cliente mínimo de Google (OAuth 2.0 con PKCE y Drive v3) con transporte intercambiable.

Solo lo que Herramientas necesita con el permiso `drive.file`: autorizar, renovar, carpetas, buscar y subir con carga reanudable.
El transporte (por omisión `urllib`) se puede cambiar: las pruebas usan un Google falso y nunca salen a internet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from tvplataforma import ajustes

log = logging.getLogger("itt_herramientas.drive")
ALCANCES = "openid email https://www.googleapis.com/auth/drive.file"
TROZO = 8 * 1024 * 1024                         # múltiplo de 256 KiB, como pide la carga reanudable
CARPETA = "application/vnd.google-apps.folder"
PORTADA = {
    "autorizar": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "revocar": "https://oauth2.googleapis.com/revoke",
    "usuario": "https://openidconnect.googleapis.com/v1/userinfo", "drive": "https://www.googleapis.com/drive/v3", "subida": "https://www.googleapis.com/upload/drive/v3",
}


class ErrorGoogle(Exception):
    def __init__(self, mensaje: str, estado: int = 0, motivo: str = ""):
        super().__init__(mensaje)
        self.estado, self.motivo = estado, motivo


class Reautorizar(ErrorGoogle):
    """Google rechazó el token de actualización (revocado, vencido o la persona quitó el acceso): hay que volver a conectar."""


class TokenVencido(ErrorGoogle):
    """El token de acceso caducó (401): se renueva y se repite la petición."""


class NoDisponible(ErrorGoogle):
    """El archivo o la carpeta no existe, está en la papelera o la app ya no tiene acceso a ella (404/403)."""


class Temporal(ErrorGoogle):
    """Falla pasajera (sin red, 429, 5xx, límite de uso): se reintenta más tarde."""


@dataclass
class Respuesta:
    estado: int
    cabeceras: dict = field(default_factory=dict)       # nombres en minúsculas
    cuerpo: bytes = b""

    def json(self) -> dict:
        try:
            return json.loads(self.cuerpo.decode("utf-8") or "{}")
        except ValueError:
            return {}


class TransporteUrllib:
    def peticion(self, metodo: str, url: str, cabeceras: dict | None = None, cuerpo: bytes | None = None, timeout: float = 60) -> Respuesta:
        req = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Respuesta(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as exc:
            return Respuesta(exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            log.warning("Sin conexión con Google: %s: %s", type(exc).__name__, exc)
            raise Temporal("No hay conexión con Google en este momento. Se reintenta solo.") from exc


def pkce() -> tuple[str, str]:
    """(verificador, desafío S256) de PKCE."""
    verificador = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("'", "\\'")


class Google:
    def __init__(self, transporte=None):
        self.transporte = transporte or TransporteUrllib()
        self.puertas = {**PORTADA, **(getattr(settings, "HERRAMIENTAS_GOOGLE", None) or {})}

    # -- OAuth --------------------------------------------------------------------------------------------------------------
    def configurado(self) -> bool:
        return bool(ajustes.valor("TV_DRIVE_CLIENT_ID") and ajustes.valor("TV_DRIVE_CLIENT_SECRET"))

    def url_autorizacion(self, redirect_uri: str, state: str, desafio: str) -> str:
        parametros = {"response_type": "code", "client_id": ajustes.valor("TV_DRIVE_CLIENT_ID"), "redirect_uri": redirect_uri, "scope": ALCANCES, "state": state,
                      "code_challenge": desafio, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
        return f"{self.puertas['autorizar']}?{urllib.parse.urlencode(parametros)}"

    def _token(self, formulario: dict) -> dict:
        formulario = {**formulario, "client_id": ajustes.valor("TV_DRIVE_CLIENT_ID"), "client_secret": ajustes.valor("TV_DRIVE_CLIENT_SECRET")}
        r = self.transporte.peticion("POST", self.puertas["token"], {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode(formulario).encode())
        datos = r.json()
        if r.estado == 200 and datos.get("access_token"):
            return datos
        error = datos.get("error", "")
        if error in ("invalid_grant", "unauthorized_client", "invalid_client", "access_denied") or r.estado in (400, 401):
            log.warning("Google rechazó la autorización (%s): %s", r.estado, datos.get("error_description") or error)
            raise Reautorizar("Google rechazó la autorización. Vuelve a conectar Drive.", r.estado, error)
        raise Temporal("Google no respondió. Se reintenta solo.", r.estado, error)

    def canjear(self, codigo: str, redirect_uri: str, verificador: str) -> dict:
        return self._token({"grant_type": "authorization_code", "code": codigo, "redirect_uri": redirect_uri, "code_verifier": verificador})

    def renovar(self, token_actualizacion: str) -> dict:
        return self._token({"grant_type": "refresh_token", "refresh_token": token_actualizacion})

    def revocar(self, token: str, timeout: float = 60) -> None:
        try:
            self.transporte.peticion("POST", self.puertas["revocar"], {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode({"token": token}).encode(),
                                     timeout=timeout)
        except ErrorGoogle:
            pass                                                                    # revocar es de buena fe: de todos modos se olvida el token

    def correo(self, acceso: str) -> str:
        r = self.transporte.peticion("GET", self.puertas["usuario"], {"Authorization": f"Bearer {acceso}"})
        return str(r.json().get("email", "")) if r.estado == 200 else ""

    # -- Drive ----------------------------------------------------------------------------------------------------------------
    def _drive(self, acceso: str, metodo: str, ruta: str, parametros: dict | None = None, cuerpo: dict | None = None) -> dict:
        url = f"{self.puertas['drive']}{ruta}"
        if parametros:
            url += "?" + urllib.parse.urlencode({**parametros, "supportsAllDrives": "true"})
        cabeceras = {"Authorization": f"Bearer {acceso}", "Accept": "application/json"}
        datos = None
        if cuerpo is not None:
            cabeceras["Content-Type"] = "application/json"
            datos = json.dumps(cuerpo).encode()
        r = self.transporte.peticion(metodo, url, cabeceras, datos)
        self._revisar(r)
        return r.json()

    @staticmethod
    def _revisar(r: Respuesta) -> None:
        if r.estado < 300:
            return
        motivo = ""
        try:
            motivo = (r.json().get("error", {}).get("errors") or [{}])[0].get("reason", "")
        except AttributeError:
            pass
        log.warning("Google Drive respondió %s %s", r.estado, motivo)
        if r.estado == 401:
            raise TokenVencido("La sesión con Google caducó.", r.estado, motivo)
        if r.estado == 404 or (r.estado == 403 and motivo in ("notFound", "forbidden", "insufficientFilePermissions", "appNotAuthorizedToFile")):
            raise NoDisponible("Google no deja ver esa carpeta o ese archivo a esta app.", r.estado, motivo)
        if r.estado == 429 or r.estado >= 500 or (r.estado == 403 and motivo in ("rateLimitExceeded", "userRateLimitExceeded", "backendError", "dailyLimitExceeded")):
            raise Temporal("Google Drive no responde por ahora. Se reintenta solo.", r.estado, motivo)
        if r.estado == 403 and motivo == "storageQuotaExceeded":
            raise ErrorGoogle("Tu Google Drive no tiene espacio. Libera espacio y se reintenta solo.", r.estado, motivo)
        raise ErrorGoogle("Google Drive rechazó la operación. Si se repite, vuelve a conectar Drive.", r.estado, motivo)

    def archivo(self, acceso: str, id_: str, campos: str = "id,name,mimeType,trashed,md5Checksum,size") -> dict:
        datos = self._drive(acceso, "GET", f"/files/{urllib.parse.quote(id_, safe='')}", {"fields": campos})
        if datos.get("trashed"):
            raise NoDisponible("El archivo está en la papelera.", 404, "trashed")
        return datos

    def buscar(self, acceso: str, padre: str, nombre: str | None = None, carpeta: bool | None = None) -> list[dict]:
        q = [f"'{_escapar(padre)}' in parents", "trashed = false"]
        if nombre is not None:
            q.append(f"name = '{_escapar(nombre)}'")
        if carpeta is not None:
            q.append(f"mimeType {'=' if carpeta else '!='} '{CARPETA}'")
        resultado, pagina = [], None
        while True:
            p = {"q": " and ".join(q), "fields": "nextPageToken,files(id,name,mimeType,md5Checksum,size)", "pageSize": "100"}
            if pagina:
                p["pageToken"] = pagina
            d = self._drive(acceso, "GET", "/files", p)
            resultado += d.get("files", [])
            pagina = d.get("nextPageToken")
            if not pagina:
                return resultado

    def crear_carpeta(self, acceso: str, nombre: str, padre: str | None = None) -> dict:
        cuerpo = {"name": nombre, "mimeType": CARPETA, **({"parents": [padre]} if padre else {})}
        return self._drive(acceso, "POST", "/files", {"fields": "id,name"}, cuerpo)

    def subir(self, acceso: str, ruta: Path, nombre: str, padre: str, tipo: str, trozo: int = TROZO, intentos: int = 5) -> dict:
        """Carga reanudable por trozos. Si un trozo falla, pregunta hasta dónde llegó Google y sigue desde ahí."""
        total = ruta.stat().st_size
        cabeceras = {"Authorization": f"Bearer {acceso}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": tipo, "X-Upload-Content-Length": str(total)}
        url = f"{self.puertas['subida']}/files?" + urllib.parse.urlencode({"uploadType": "resumable", "supportsAllDrives": "true", "fields": "id,name,md5Checksum,size"})
        r = self.transporte.peticion("POST", url, cabeceras, json.dumps({"name": nombre, "parents": [padre]}).encode())
        self._revisar(r)
        sesion = r.cabeceras.get("location")
        if not sesion:
            raise ErrorGoogle("Google no abrió la carga del archivo.", r.estado)
        enviado = 0
        fallos = 0
        with open(ruta, "rb") as f:
            while True:
                f.seek(enviado)
                datos = f.read(min(trozo, total - enviado)) if total else b""
                fin = enviado + len(datos)
                rango = f"bytes {enviado}-{fin - 1}/{total}" if datos else f"bytes */{total}"
                try:
                    r = self.transporte.peticion("PUT", sesion, {"Content-Length": str(len(datos)), "Content-Range": rango}, datos, timeout=120)
                except Temporal:
                    r = None
                if r is not None and r.estado in (200, 201):
                    return r.json()
                if r is not None and r.estado == 308:
                    fallos = 0
                    enviado = self._avance(r, enviado, fin)
                    continue
                if r is not None and r.estado in (404, 410):
                    raise Temporal("La carga reanudable caducó; se empezará de nuevo.", r.estado)
                if r is not None and r.estado < 500 and r.estado not in (408, 429):
                    self._revisar(r)
                fallos += 1
                if fallos > intentos:
                    raise Temporal("La subida a Drive se interrumpió varias veces.", r.estado if r is not None else 0)
                enviado = self._consultar(sesion, total, enviado)                  # ¿hasta dónde llegó Google?
                if isinstance(enviado, dict):
                    return enviado

    @staticmethod
    def _avance(r: Respuesta, actual: int, tope: int) -> int:
        rango = r.cabeceras.get("range", "")
        return int(rango.split("-")[1]) + 1 if rango.startswith("bytes=0-") else 0

    def _consultar(self, sesion: str, total: int, actual: int):
        try:
            r = self.transporte.peticion("PUT", sesion, {"Content-Length": "0", "Content-Range": f"bytes */{total}"})
        except Temporal:
            return actual
        if r.estado in (200, 201):
            return r.json()
        if r.estado == 308:
            return self._avance(r, actual, total)
        return actual
