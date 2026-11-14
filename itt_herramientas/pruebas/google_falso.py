"""Google falso para las pruebas de Drive: OAuth 2.0 con PKCE y la parte de Drive v3 que usa Herramientas, con el permiso `drive.file`.

Hace cumplir lo que importa de `drive.file`: la app solo ve lo que ella creó o lo que la persona le «concedió» con Picker (`conceder`).
Permite inyectar fallas (`fallar`) para probar caducidad de token, límites de uso, 5xx y cortes a media subida. Nunca sale a internet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import uuid

from itt_herramientas.drive.google import CARPETA, Respuesta, Temporal


class GoogleFalso:
    def __init__(self, correo="persona@example.com", client_id="cliente-de-prueba"):
        self.correo, self.client_id = correo, client_id
        self.codigos: dict[str, dict] = {}                # código -> {desafio, redirect}
        self.refrescos: set[str] = set()
        self.accesos: set[str] = set()
        self.archivos: dict[str, dict] = {}
        self.concedidos: set[str] = set()                 # ids que la persona abrió con Picker
        self.sesiones: dict[str, dict] = {}
        self.llamadas: list[tuple[str, str]] = []
        self.revocados: list[str] = []
        self.fallas: list[tuple[str, int]] = []           # (fragmento de la URL, estado) que se aplican una vez
        self.cortes: list[int] = []                       # bytes tras los cuales se corta UN PUT de subida
        self.caduca_acceso = False

    # -- Preparación de las pruebas ---------------------------------------------------------------------------------------------
    def carpeta(self, nombre="GRUPO ITT REPORTES", padre=None, conceder=True) -> str:
        id_ = "c" + uuid.uuid4().hex[:20]
        self.archivos[id_] = {"id": id_, "name": nombre, "mimeType": CARPETA, "parents": [padre] if padre else [], "trashed": False}
        if conceder:
            self.concedidos.add(id_)
        return id_

    def conceder(self, id_: str) -> None:
        self.concedidos.add(id_)

    def fallar(self, fragmento: str, estado: int) -> None:
        self.fallas.append((fragmento, estado))

    def codigo(self, desafio: str, redirect: str) -> str:
        c = "cod" + uuid.uuid4().hex[:12]
        self.codigos[c] = {"desafio": desafio, "redirect": redirect}
        return c

    def hijos(self, id_: str) -> list[dict]:
        return [a for a in self.archivos.values() if id_ in a["parents"] and not a["trashed"]]

    def ruta_de(self, id_: str) -> str:
        a = self.archivos[id_]
        return f"{self.ruta_de(a['parents'][0])}/{a['name']}" if a["parents"] else a["name"]

    def _visible(self, id_: str) -> bool:
        while id_:
            a = self.archivos.get(id_)
            if a is None:
                return False
            if id_ in self.concedidos or a.get("creado_por_app"):
                return True
            id_ = a["parents"][0] if a["parents"] else ""
        return False

    # -- Transporte -------------------------------------------------------------------------------------------------------------
    def peticion(self, metodo, url, cabeceras=None, cuerpo=None, timeout=0) -> Respuesta:
        self.llamadas.append((metodo, url))
        for i, (frag, estado) in enumerate(self.fallas):
            if frag in url:
                del self.fallas[i]
                return Respuesta(estado, {}, json.dumps({"error": {"errors": [{"reason": {429: "rateLimitExceeded", 500: "backendError", 404: "notFound"}.get(estado, "other")}]}}).encode())
        cabeceras = cabeceras or {}
        p = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(p.query))
        if p.netloc == "oauth2.googleapis.com" and p.path == "/token":
            return self._token(dict(urllib.parse.parse_qsl((cuerpo or b"").decode())))
        if p.netloc == "oauth2.googleapis.com" and p.path == "/revoke":
            self.revocados.append(dict(urllib.parse.parse_qsl((cuerpo or b"").decode())).get("token", ""))
            self.refrescos.discard(self.revocados[-1])
            return Respuesta(200)
        if p.netloc == "openidconnect.googleapis.com":
            return Respuesta(200, {}, json.dumps({"email": self.correo}).encode()) if self._acceso(cabeceras) else Respuesta(401)
        if p.netloc == "fake.upload":
            return self._poner(p.path.rsplit("/", 1)[1], cabeceras, cuerpo or b"")
        if not self._acceso(cabeceras):
            return Respuesta(401, {}, b"{}")
        if p.path.startswith("/upload/drive/v3/files"):
            return self._abrir_subida(json.loads(cuerpo or b"{}"), cabeceras)
        if p.path == "/drive/v3/files" and metodo == "GET":
            return self._listar(q["q"])
        if p.path == "/drive/v3/files" and metodo == "POST":
            return self._crear(json.loads(cuerpo or b"{}"))
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", p.path)
        if m and metodo == "GET":
            a = self.archivos.get(urllib.parse.unquote(m.group(1)))
            if a is None or not self._visible(a["id"]):
                return Respuesta(404, {}, json.dumps({"error": {"errors": [{"reason": "notFound"}]}}).encode())
            return Respuesta(200, {}, json.dumps(self._publico(a)).encode())
        return Respuesta(404)

    # -- OAuth -------------------------------------------------------------------------------------------------------------------
    def _token(self, f: dict) -> Respuesta:
        if f.get("client_id") != self.client_id:
            return Respuesta(401, {}, json.dumps({"error": "invalid_client"}).encode())
        if f["grant_type"] == "authorization_code":
            c = self.codigos.pop(f.get("code", ""), None)
            desafio = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if c is None or c["desafio"] != desafio or c["redirect"] != f.get("redirect_uri"):
                return Respuesta(400, {}, json.dumps({"error": "invalid_grant", "error_description": "Bad Request"}).encode())
            refresco = "r-" + uuid.uuid4().hex
            self.refrescos.add(refresco)
            return Respuesta(200, {}, json.dumps({"access_token": self._nuevo_acceso(), "refresh_token": refresco, "expires_in": 3599, "scope": "drive.file"}).encode())
        if f["grant_type"] == "refresh_token":
            if f.get("refresh_token") not in self.refrescos:
                return Respuesta(400, {}, json.dumps({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}).encode())
            return Respuesta(200, {}, json.dumps({"access_token": self._nuevo_acceso(), "expires_in": 3599}).encode())
        return Respuesta(400, {}, json.dumps({"error": "unsupported_grant_type"}).encode())

    def _nuevo_acceso(self) -> str:
        a = "a-" + uuid.uuid4().hex
        self.accesos.add(a)
        return a

    def vencer_accesos(self) -> None:
        self.accesos.clear()

    def revocar_en_google(self) -> None:
        self.refrescos.clear()

    def _acceso(self, cabeceras) -> bool:
        return cabeceras.get("Authorization", "").removeprefix("Bearer ") in self.accesos

    # -- Drive ------------------------------------------------------------------------------------------------------------------
    def _en_papelera(self, a: dict) -> bool:
        while a:
            if a["trashed"]:
                return True
            a = self.archivos.get(a["parents"][0]) if a["parents"] else None
        return False

    def _publico(self, a: dict) -> dict:
        return {**{k: v for k, v in a.items() if k in ("id", "name", "mimeType", "md5Checksum", "size")}, "trashed": self._en_papelera(a)}

    def _listar(self, q: str) -> Respuesta:
        padre = re.search(r"'((?:[^'\\]|\\.)*)' in parents", q).group(1)
        if not self._visible(padre):
            return Respuesta(404, {}, json.dumps({"error": {"errors": [{"reason": "notFound"}]}}).encode())
        r = self.hijos(padre)
        n = re.search(r"name = '((?:[^'\\]|\\.)*)'", q)
        if n:
            nombre = n.group(1).replace("\\'", "'").replace("\\\\", "\\")
            r = [a for a in r if a["name"] == nombre]
        if "mimeType = " in q:
            r = [a for a in r if a["mimeType"] == CARPETA]
        elif "mimeType != " in q:
            r = [a for a in r if a["mimeType"] != CARPETA]
        return Respuesta(200, {}, json.dumps({"files": [self._publico(a) for a in r]}).encode())

    def _crear(self, c: dict) -> Respuesta:
        padres = c.get("parents") or []
        if padres and not self._visible(padres[0]):
            return Respuesta(404, {}, json.dumps({"error": {"errors": [{"reason": "notFound"}]}}).encode())
        id_ = self.carpeta(c["name"], padres[0] if padres else None, conceder=False)
        self.archivos[id_]["creado_por_app"] = True
        return Respuesta(200, {}, json.dumps({"id": id_, "name": c["name"]}).encode())

    def _abrir_subida(self, meta: dict, cabeceras) -> Respuesta:
        padres = meta.get("parents") or []
        if not padres or not self._visible(padres[0]):
            return Respuesta(404, {}, json.dumps({"error": {"errors": [{"reason": "notFound"}]}}).encode())
        sid = uuid.uuid4().hex
        self.sesiones[sid] = {"meta": meta, "datos": b"", "total": int(cabeceras["X-Upload-Content-Length"])}
        return Respuesta(200, {"location": f"https://fake.upload/{sid}"}, b"")

    def _poner(self, sid: str, cabeceras, datos: bytes) -> Respuesta:
        s = self.sesiones.get(sid)
        if s is None:
            return Respuesta(404)
        rango = cabeceras.get("Content-Range", "")
        if rango.startswith("bytes */"):
            return self._estado(s)
        inicio = int(re.match(r"bytes (\d+)-", rango).group(1))
        if inicio != len(s["datos"]):
            return Respuesta(308, {"range": f"bytes=0-{len(s['datos']) - 1}"} if s["datos"] else {})
        if self.cortes and len(datos) > 0:
            tope = self.cortes.pop(0)
            s["datos"] += datos[:tope]                              # llegó una parte y se cortó la conexión
            raise Temporal("conexión cortada (prueba)")
        s["datos"] += datos
        if len(s["datos"]) >= s["total"]:
            return self._terminar(s)
        return Respuesta(308, {"range": f"bytes=0-{len(s['datos']) - 1}"})

    def _estado(self, s: dict) -> Respuesta:
        if len(s["datos"]) >= s["total"]:
            return self._terminar(s)
        return Respuesta(308, {"range": f"bytes=0-{len(s['datos']) - 1}"} if s["datos"] else {})

    def _terminar(self, s: dict) -> Respuesta:
        meta = s["meta"]
        id_ = "f" + uuid.uuid4().hex[:20]
        self.archivos[id_] = {"id": id_, "name": meta["name"], "mimeType": "application/octet-stream", "parents": meta["parents"], "trashed": False, "creado_por_app": True,
                              "md5Checksum": hashlib.md5(s["datos"]).hexdigest(), "size": str(len(s["datos"])), "contenido": s["datos"]}
        return Respuesta(200, {}, json.dumps({"id": id_, "name": meta["name"], "md5Checksum": self.archivos[id_]["md5Checksum"], "size": str(len(s["datos"]))}).encode())
