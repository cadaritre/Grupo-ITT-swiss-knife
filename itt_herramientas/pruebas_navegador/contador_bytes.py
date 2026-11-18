"""Un proxy inverso que cuenta los bytes en el cable, en los dos sentidos y por petición (método, ruta, estado, bytes pedidos y recibidos).

Se pone entre el navegador de la prueba y el servidor de pruebas: el navegador solo habla con el proxy, así que todo lo que sale de la
página, de su marco y de su service worker pasa por aquí, sea cual sea el motor. Cuenta lo que de verdad viaja en HTTP/1.1: la línea de
la petición, sus cabeceras (con la cookie) y su cuerpo; la línea de estado, las cabeceras y el cuerpo de la respuesta, tal como se
mandan (con la compresión y el troceado).

Hace lo que hace Caddy en producción (Portal, `despliegue/caddy`), para que lo medido sea lo que viaja allá:
- `encode zstd gzip` de las páginas de Django: gzip de lo comprimible (los tipos por omisión de Caddy: texto, JS, JSON, CSS, SVG, fuentes,
  WebAssembly) cuando el navegador lo acepta, la respuesta mide al menos 512 bytes y no viene ya comprimida (zstd comprime algo más: lo
  contado es un poco más que lo de producción). Los planos (DWG, DXF) viajan tal cual, como en producción.
- `/cad/editor/…`: el `.gz` que deja `preparar.py` junto a cada archivo grande (`precompressed gzip`).
- `/static/…`: un nombre con la huella de collectstatic (12 hex) se guarda un año (`immutable`); uno sin huella, 5 minutos. En producción además va HTTP/2 o HTTP/3, que comprime las cabeceras repetidas (HPACK/QPACK): lo contado
aquí es el peor caso.

Uso:
    contador = ContadorBytes("localhost", 8081).arrancar()
    contador.url          # http://localhost:<puerto>, la dirección para el navegador
    contador.marcar("b")  # empieza un tramo; contador.tramo("b") da sus peticiones
    contador.detener()
"""
from __future__ import annotations

import re
import socket
import socketserver
import threading
import time
import zlib
from dataclasses import dataclass, field

COMPRIMIBLE = re.compile(r"^(text/|font/|application/(json|javascript|x-javascript|wasm|xml|xhtml\+xml|manifest\+json|ld\+json|geo\+json|font|"
                         r"ttf|otf|truetype|opentype|x-font|vnd\.ms-fontobject)|image/(svg\+xml|x-icon|vnd\.microsoft\.icon))", re.I)
#: Lo que `preparar.py` deja precomprimido junto al editor (COMPRIMIR, más de 1 KB).
PRECOMPRIMIDO = (".js", ".wasm", ".json", ".svg", ".ttf", ".html", ".css")
ESTATICO_CON_HUELLA = re.compile(r"\.[0-9a-f]{12}\.[A-Za-z0-9]+$")
MINIMO_COMPRIMIR = 512
TROZO = 64 * 1024


@dataclass
class Peticion:
    t: float
    metodo: str
    ruta: str
    estado: int = 0
    pedido: int = 0                 # bytes del navegador al servidor (cabeceras + cuerpo)
    respuesta: int = 0              # bytes del servidor al navegador (cabeceras + cuerpo, comprimido si aplica)
    cuerpo: int = 0                 # cuerpo de la respuesta sin comprimir
    gzip: bool = False
    tipo: str = ""
    terminada: bool = False
    cortada: bool = False           # llegó con la red «cortada» (modo avión): no pasó al servidor ni tuvo respuesta (0 bytes)
    intento: int = 0                # lo que el navegador intentó mandar con la red cortada

    @property
    def total(self) -> int:
        return self.pedido + self.respuesta


@dataclass
class _Estado:
    peticiones: list = field(default_factory=list)
    marcas: dict = field(default_factory=dict)
    en_curso: int = 0
    ultima: float = 0.0


class _Fin(Exception):
    pass


def _leer_cabeza(s: socket.socket, resto: bytearray) -> bytes | None:
    """Hasta el fin de las cabeceras (\\r\\n\\r\\n); lo que sobra queda en `resto`. None si la conexión se cerró sin nada."""
    while True:
        i = resto.find(b"\r\n\r\n")
        if i >= 0:
            cabeza = bytes(resto[: i + 4])
            del resto[: i + 4]
            return cabeza
        trozo = s.recv(TROZO)
        if not trozo:
            if resto:
                raise _Fin("cabeceras cortadas")
            return None
        resto += trozo
        if len(resto) > 1024 * 1024:
            raise _Fin("cabeceras demasiado grandes")


def _leer_n(s: socket.socket, resto: bytearray, n: int):
    """Genera `n` bytes del cuerpo (primero lo que ya llegó en `resto`)."""
    while n > 0:
        if resto:
            parte = bytes(resto[:n])
            del resto[: len(parte)]
        else:
            parte = s.recv(min(TROZO, n))
            if not parte:
                raise _Fin("cuerpo cortado")
        n -= len(parte)
        yield parte


def _linea(s: socket.socket, resto: bytearray) -> bytes:
    while True:
        i = resto.find(b"\r\n")
        if i >= 0:
            linea = bytes(resto[: i + 2])
            del resto[: i + 2]
            return linea
        trozo = s.recv(TROZO)
        if not trozo:
            raise _Fin("troceado cortado")
        resto += trozo


def _leer_troceado(s: socket.socket, resto: bytearray, crudo: list | None = None):
    """Genera los datos de un cuerpo troceado (chunked). `crudo` junta el tamaño del cable si se pide."""
    while True:
        linea = _linea(s, resto)
        if crudo is not None:
            crudo.append(len(linea))
        tam = int(linea.split(b";")[0].strip() or b"0", 16)
        if tam == 0:
            while True:                                         # trailers hasta la línea vacía
                fin = _linea(s, resto)
                if crudo is not None:
                    crudo.append(len(fin))
                if fin == b"\r\n":
                    return
        for parte in _leer_n(s, resto, tam):
            if crudo is not None:
                crudo.append(len(parte))
            yield parte
        cola = _linea(s, resto)
        if crudo is not None:
            crudo.append(len(cola))


def _cabeceras(cabeza: bytes) -> tuple[str, list[tuple[str, str]]]:
    lineas = cabeza.decode("latin-1").split("\r\n")
    primera = lineas[0]
    pares = []
    for l in lineas[1:]:
        if not l:
            continue
        k, _, v = l.partition(":")
        pares.append((k.strip(), v.strip()))
    return primera, pares


def _valor(pares, nombre) -> str:
    nombre = nombre.lower()
    for k, v in pares:
        if k.lower() == nombre:
            return v
    return ""


def _armar(primera: str, pares) -> bytes:
    return (primera + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in pares) + "\r\n").encode("latin-1")


class ContadorBytes:
    def __init__(self, host: str, puerto: int, comprimir: bool = True):
        self.destino = (host, int(puerto))
        self.comprimir = comprimir
        self._e = _Estado()
        self._candado = threading.Lock()
        self._servidor = None
        #: Modo avión del lado de la red: lo que llegue se anota y se corta, sin pasar al servidor. Playwright (`set_offline`) no corta
        #: todo: Chromium busca la versión nueva del service worker por fuera de la página y esa petición sí salía.
        self.cortar = False

    # -- registro ---------------------------------------------------------------------------------------------------------------
    def _nueva(self, metodo, ruta) -> Peticion:
        p = Peticion(t=time.monotonic(), metodo=metodo, ruta=ruta)
        with self._candado:
            self._e.peticiones.append(p)
            self._e.en_curso += 1
            self._e.ultima = time.monotonic()
        return p

    def _cerrar(self, p: Peticion):
        with self._candado:
            p.terminada = True
            self._e.en_curso -= 1
            self._e.ultima = time.monotonic()

    def marcar(self, nombre: str) -> None:
        with self._candado:
            self._e.marcas[nombre] = len(self._e.peticiones)

    def tramo(self, desde: str, hasta: str | None = None) -> list[Peticion]:
        with self._candado:
            i = self._e.marcas[desde]
            j = self._e.marcas[hasta] if hasta else len(self._e.peticiones)
            return list(self._e.peticiones[i:j])

    def todas(self) -> list[Peticion]:
        with self._candado:
            return list(self._e.peticiones)

    def esperar_quieto(self, calma: float = 1.5, tope: float = 60.0) -> None:
        """Hasta que no haya peticiones en curso durante `calma` s."""
        limite = time.monotonic() + tope
        while time.monotonic() < limite:
            with self._candado:
                quieto = self._e.en_curso <= 0 and time.monotonic() - self._e.ultima >= calma
            if quieto:
                return
            time.sleep(0.1)

    # -- servidor ---------------------------------------------------------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"http://localhost:{self._servidor.server_address[1]}"

    def arrancar(self) -> "ContadorBytes":
        contador = self

        class Manejador(socketserver.BaseRequestHandler):
            def handle(self):
                contador._conexion(self.request)

        class Servidor(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._servidor = Servidor(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self._servidor.serve_forever, name="contador-bytes", daemon=True).start()
        return self

    def detener(self) -> None:
        if self._servidor:
            self._servidor.shutdown()
            self._servidor.server_close()

    def _conexion(self, cliente: socket.socket) -> None:
        cliente.settimeout(600)
        resto = bytearray()
        try:
            while True:
                cabeza = _leer_cabeza(cliente, resto)
                if cabeza is None:
                    return
                if not self._una(cliente, resto, cabeza):
                    return
        except (_Fin, OSError, ValueError):
            return
        finally:
            try:
                cliente.close()
            except OSError:
                pass

    def _una(self, cliente: socket.socket, resto: bytearray, cabeza: bytes) -> bool:
        primera, pares = _cabeceras(cabeza)
        metodo, ruta, _version = (primera.split(" ") + ["", ""])[:3]
        p = self._nueva(metodo, ruta)
        if self.cortar:
            p.cortada, p.intento = True, len(cabeza)
            self._cerrar(p)
            return False
        try:
            # El cuerpo de la petición (Content-Length o troceado), contado tal como llega.
            cuerpo_pedido = []
            if _valor(pares, "transfer-encoding").lower() == "chunked":
                crudo = []
                cuerpo_pedido = list(_leer_troceado(cliente, resto, crudo))
                p.pedido = len(cabeza) + sum(crudo)
                datos = b"".join(cuerpo_pedido)
                pares = [(k, v) for k, v in pares if k.lower() != "transfer-encoding"] + [("Content-Length", str(len(datos)))]
            else:
                n = int(_valor(pares, "content-length") or 0)
                datos = b"".join(_leer_n(cliente, resto, n)) if n else b""
                p.pedido = len(cabeza) + len(datos)
            sigue = _valor(pares, "connection").lower() != "close"
            acepta_gzip = "gzip" in _valor(pares, "accept-encoding").lower()
            # Al servidor: una conexión por petición (Connection: close), sin compresión (la pone este proxy, como Caddy).
            arriba_pares = [(k, v) for k, v in pares if k.lower() not in ("connection", "keep-alive", "accept-encoding", "proxy-connection")]
            arriba_pares.append(("Connection", "close"))
            arriba = socket.create_connection(self.destino, timeout=600)
            try:
                arriba.sendall(_armar(primera, arriba_pares) + datos)
                return self._respuesta(cliente, arriba, p, metodo, acepta_gzip) and sigue
            finally:
                arriba.close()
        finally:
            self._cerrar(p)

    def _respuesta(self, cliente: socket.socket, arriba: socket.socket, p: Peticion, metodo: str, acepta_gzip: bool) -> bool:
        resto = bytearray()
        while True:
            cabeza = _leer_cabeza(arriba, resto)
            if cabeza is None:
                raise _Fin("el servidor cerró sin responder")
            primera, pares = _cabeceras(cabeza)
            estado = int(primera.split(" ")[1])
            if estado != 100:
                break
        p.estado = estado
        p.tipo = _valor(pares, "content-type")
        sin_cuerpo = metodo == "HEAD" or estado in (204, 304) or 100 <= estado < 200
        largo = _valor(pares, "content-length")
        troceado = _valor(pares, "transfer-encoding").lower() == "chunked"
        if sin_cuerpo:
            cuerpo = iter(())
        elif troceado:
            cuerpo = _leer_troceado(arriba, resto)
        elif largo:
            cuerpo = _leer_n(arriba, resto, int(largo))
        else:
            def hasta_cerrar():
                if resto:
                    yield bytes(resto)
                    resto.clear()
                while trozo := arriba.recv(TROZO):
                    yield trozo
            cuerpo = hasta_cerrar()
        base = [(k, v) for k, v in pares if k.lower() not in ("connection", "keep-alive", "transfer-encoding", "content-length")]
        camino = p.ruta.split("?")[0]
        if camino.startswith("/static/") and estado == 200:              # las cabeceras de caché de Caddy para los estáticos
            base = [(k, v) for k, v in base if k.lower() != "cache-control"]
            base.append(("Cache-Control", "public, max-age=31536000, immutable" if ESTATICO_CON_HUELLA.search(camino) else "public, max-age=300"))
        if camino.startswith("/cad/editor/"):
            tipo_ok = camino.endswith(PRECOMPRIMIDO) and not (largo and int(largo) <= 1024)
        else:
            tipo_ok = bool(COMPRIMIBLE.match(p.tipo or "")) and not (largo and int(largo) < MINIMO_COMPRIMIR)
        comprimir = self.comprimir and acepta_gzip and not sin_cuerpo and estado == 200 and not _valor(pares, "content-encoding") and tipo_ok
        if comprimir:
            vary = _valor(base, "vary")
            base = [(k, v) for k, v in base if k.lower() != "vary"]
            base += [("Content-Encoding", "gzip"), ("Vary", f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"), ("Transfer-Encoding", "chunked")]
            p.gzip = True
        elif sin_cuerpo:
            if largo and metodo == "HEAD":
                base.append(("Content-Length", largo))
        elif largo:
            base.append(("Content-Length", largo))
        else:
            base.append(("Transfer-Encoding", "chunked"))
        base.append(("Connection", "keep-alive"))
        cab = _armar(primera, base)
        cliente.sendall(cab)
        enviados = len(cab)
        if not sin_cuerpo:
            if comprimir or not largo:
                z = zlib.compressobj(6, zlib.DEFLATED, 31) if comprimir else None
                for parte in cuerpo:
                    p.cuerpo += len(parte)
                    sale = z.compress(parte) if z else parte
                    if sale:
                        trozo = b"%x\r\n" % len(sale) + sale + b"\r\n"
                        cliente.sendall(trozo)
                        enviados += len(trozo)
                if z:
                    sale = z.flush()
                    if sale:
                        trozo = b"%x\r\n" % len(sale) + sale + b"\r\n"
                        cliente.sendall(trozo)
                        enviados += len(trozo)
                cliente.sendall(b"0\r\n\r\n")
                enviados += 5
            else:
                for parte in cuerpo:
                    p.cuerpo += len(parte)
                    cliente.sendall(parte)
                    enviados += len(parte)
        p.respuesta = enviados
        return True


def resumen(peticiones: list[Peticion]) -> dict:
    return {"peticiones": len(peticiones), "pedido": sum(p.pedido for p in peticiones), "respuesta": sum(p.respuesta for p in peticiones),
            "total": sum(p.total for p in peticiones)}


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HUELLA = re.compile(r"\.[0-9a-f]{12}\.")


def agrupar(peticiones: list[Peticion]) -> list[dict]:
    """Por método y ruta (sin la consulta, con <id> en lugar del UUID y sin la huella de los estáticos), de más a menos bytes."""
    grupos: dict = {}
    for p in peticiones:
        ruta = _HUELLA.sub(".", _UUID.sub("<id>", p.ruta.split("?")[0]))
        if ruta.startswith("/cad/editor/"):
            ruta = "/cad/editor/…"
        if ruta.startswith("/static/"):
            ruta = "/static/…"
        g = grupos.setdefault((p.metodo, ruta), {"metodo": p.metodo, "ruta": ruta, "n": 0, "pedido": 0, "respuesta": 0, "estados": set()})
        g["n"] += 1
        g["pedido"] += p.pedido
        g["respuesta"] += p.respuesta
        g["estados"].add(p.estado)
    filas = sorted(grupos.values(), key=lambda g: -(g["pedido"] + g["respuesta"]))
    for g in filas:
        g["estados"] = sorted(g["estados"])
    return filas
