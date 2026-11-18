"""Rásteres sintéticos para las pruebas de «Carpeta del proyecto» (CAD): TIFF y BigTIFF de cada disposición y compresión, GeoTIFF,
archivos de mundo y el juego de fachadas «tipo PointCab» (cientos de imágenes con su DXF).

Todo se genera aquí, con un patrón que se puede comprobar píxel a píxel: rojo = x mod 256, verde = y mod 256, azul = (x xor y) mod 256
(en el nivel completo; las vistas generales llevan el promedio de cada bloque). Nada viene de proyectos reales.

El escritor de TIFF es propio (solo biblioteca estándar, numpy y Pillow para JPEG): escribe los bloques en orden y los directorios al final,
así un archivo de varios GB sale en flujo, sin tenerlo en memoria. Uso desde la línea de comandos para las mediciones grandes:

    python -m itt_herramientas.pruebas_navegador.cad_local_rasters grande DIR --gb 3.2 --mosaico 512
    python -m itt_herramientas.pruebas_navegador.cad_local_rasters grande DIR --gb 3.2 --tiras 16
    python -m itt_herramientas.pruebas_navegador.cad_local_rasters pointcab DIR --imagenes 320
"""
from __future__ import annotations

import argparse
import io
import math
import struct
import zlib
from pathlib import Path

import numpy as np

COMPRESION = {"none": 1, "lzw": 5, "jpeg": 7, "deflate": 8, "packbits": 32773}


def patron(x0: int, y0: int, ancho: int, alto: int, spp: int = 3, alfa: bool = False) -> np.ndarray:
    """El patrón de prueba de la ventana [x0, x0+ancho) × [y0, y0+alto), como uint8 (alto, ancho, canales)."""
    xs = (np.arange(x0, x0 + ancho, dtype=np.uint32))[None, :]
    ys = (np.arange(y0, y0 + alto, dtype=np.uint32))[:, None]
    r = np.broadcast_to(xs & 255, (alto, ancho))
    g = np.broadcast_to(ys & 255, (alto, ancho))
    b = (xs ^ ys) & 255
    if spp == 1:
        return ((r.astype(np.uint16) + g) // 2).astype(np.uint8)[:, :, None]
    canales = [r, g, b]
    if alfa:
        canales.append(np.where(((xs // 64) + (ys // 64)) % 2 == 0, 255, 128).astype(np.uint32) + 0 * r)
    return np.stack(canales, axis=-1).astype(np.uint8)


def lzw(datos: bytes) -> bytes:
    """LZW de TIFF (MSB primero, cambio de ancho «temprano»)."""
    salida = bytearray()
    acumulado, bits = 0, 0
    ancho = 9

    def emitir(codigo: int) -> None:
        nonlocal acumulado, bits
        acumulado = (acumulado << ancho) | codigo
        bits += ancho
        while bits >= 8:
            bits -= 8
            salida.append((acumulado >> bits) & 255)
        acumulado &= (1 << bits) - 1

    tabla = {bytes([i]): i for i in range(256)}
    siguiente = 258
    emitir(256)
    w = b""
    for byte in datos:
        wc = w + bytes([byte])
        if wc in tabla:
            w = wc
            continue
        emitir(tabla[w])
        tabla[wc] = siguiente
        siguiente += 1
        # libtiff: el codificador cambia de ancho al pasar de (1 << ancho) − 1; el decodificador, un código antes («temprano»).
        if siguiente >= 4094:
            emitir(256)
            tabla = {bytes([i]): i for i in range(256)}
            siguiente, ancho = 258, 9
        elif siguiente > (1 << ancho) - 1 and ancho < 12:
            ancho += 1
        w = bytes([byte])
    if w:
        emitir(tabla[w])
        siguiente += 1
        if siguiente > (1 << ancho) - 1 and ancho < 12:
            ancho += 1
    emitir(257)
    if bits:
        salida.append((acumulado << (8 - bits)) & 255)
    return bytes(salida)


def packbits(datos: bytes) -> bytes:
    salida = bytearray()
    i, n = 0, len(datos)
    while i < n:
        j = i
        while j + 1 < n and datos[j + 1] == datos[i] and j - i < 127:
            j += 1
        if j > i:
            salida += struct.pack("b", -(j - i)) + datos[i:i + 1]
            i = j + 1
            continue
        j = i
        while j < n and j - i < 128 and not (j + 1 < n and datos[j + 1] == datos[j]):
            j += 1
        j = max(j, i + 1)
        salida += struct.pack("b", j - i - 1) + datos[i:j]
        i = j
    return bytes(salida)


def predecir(bloque: np.ndarray) -> np.ndarray:
    d = bloque.astype(np.int16)
    d[:, 1:, :] = d[:, 1:, :] - bloque[:, :-1, :].astype(np.int16)
    return (d & 255).astype(np.uint8)


def jpeg_partido(bloque: np.ndarray) -> tuple[bytes, bytes]:
    """JPEG del bloque (YCbCr 4:2:0) partido como TIFF lo guarda: (JPEGTables = SOI+DQT+DHT+EOI, datos sin tablas)."""
    from PIL import Image

    b = io.BytesIO()
    modo = "L" if bloque.shape[2] == 1 else "RGB"
    Image.fromarray(bloque[:, :, 0] if modo == "L" else bloque[:, :, :3], modo).save(b, "JPEG", quality=92, subsampling=2)
    j = b.getvalue()
    tablas, resto = bytearray(b"\xff\xd8"), bytearray(b"\xff\xd8")
    i = 2
    while i < len(j):
        marca = j[i + 1]
        if marca == 0xDA:
            resto += j[i:]
            break
        largo = struct.unpack(">H", j[i + 2:i + 4])[0]
        seg = j[i:i + 2 + largo]
        (tablas if marca in (0xDB, 0xC4) else resto).extend(seg if marca not in (0xE0,) else b"")
        i += 2 + largo
    tablas += b"\xff\xd9"
    return bytes(tablas), bytes(resto)


class EscritorTiff:
    """Escribe un TIFF o BigTIFF en flujo: `nivel(...)` por cada imagen (la completa primero, luego las vistas generales)."""

    def __init__(self, ruta: Path, *, grande: bool = False, mm: bool = False):
        self.f = open(ruta, "wb")
        self.grande, self.e = grande, (">" if mm else "<")
        self.f.write((b"MM" if mm else b"II") + struct.pack(self.e + "H", 43 if grande else 42))
        if grande:
            self.f.write(struct.pack(self.e + "HHQ", 8, 0, 0))
        else:
            self.f.write(struct.pack(self.e + "I", 0))
        self.directorios: list[list[tuple]] = []

    def nivel(self, ancho, alto, bloques, *, mosaico=None, filas_tira=None, spp=3, alfa=False, compresion="none", planar=1,
              predictor=1, reducida=False, geo=None, paleta=None, bits=8, fotometria=None):
        """`bloques(x0, y0, w, h)` → np.uint8 (h, w, c) del nivel. Escribe los datos y guarda el directorio para el final."""
        tw, th = (mosaico, mosaico) if mosaico else (ancho, filas_tira or alto)
        cols, filas = math.ceil(ancho / tw), math.ceil(alto / th)
        offs, cuentas, tablas = [], [], None
        planos = spp if planar == 2 else 1
        for p in range(planos):
            for f in range(filas):
                for c in range(cols):
                    x0, y0 = c * tw, f * th
                    h = th if mosaico else min(th, alto - y0)
                    w = tw
                    b = np.zeros((h, w, spp), np.uint8)
                    real = bloques(x0, y0, min(w, ancho - x0), min(h, alto - y0))
                    b[: real.shape[0], : real.shape[1], :] = real
                    if planar == 2:
                        b = b[:, :, p:p + 1]
                    if bits == 16:
                        b16 = b.astype(np.uint16) * 257
                        if predictor == 2:
                            d = b16.astype(np.int32)
                            d[:, 1:, :] -= b16[:, :-1, :]
                            b16 = (d & 65535).astype(np.uint16)
                        crudo = b16.astype(self.e + "u2").tobytes()
                    else:
                        crudo = (predecir(b) if predictor == 2 else b).tobytes()
                    if compresion == "lzw":
                        crudo = lzw(crudo)
                    elif compresion == "deflate":
                        crudo = zlib.compress(crudo, 6)
                    elif compresion == "packbits":
                        crudo = packbits(crudo)
                    elif compresion == "jpeg":
                        tablas, crudo = jpeg_partido(b)
                    offs.append(self.f.tell())
                    cuentas.append(len(crudo))
                    self.f.write(crudo)
                    if self.f.tell() % 2:
                        self.f.write(b"\0")
        foto = fotometria if fotometria is not None else (3 if paleta is not None else (6 if compresion == "jpeg" and spp >= 3 else (2 if spp >= 3 else 1)))
        et = [(254, 4, [1 if reducida else 0]), (256, 4, [ancho]), (257, 4, [alto]), (258, 3, [bits] * spp),
              (259, 3, [COMPRESION[compresion]]), (262, 3, [foto]), (277, 3, [spp]), (284, 3, [planar])]
        if predictor == 2:
            et.append((317, 3, [2]))
        if alfa:
            et.append((338, 3, [2]))
        if paleta is not None:
            et.append((320, 3, list(paleta)))
        if tablas:
            et.append((347, 7, tablas))
        if mosaico:
            et += [(322, 3, [tw]), (323, 3, [th]), (324, 16 if self.grande else 4, offs), (325, 16 if self.grande else 4, cuentas)]
        else:
            et += [(273, 16 if self.grande else 4, offs), (278, 4, [th]), (279, 16 if self.grande else 4, cuentas)]
        if geo:
            for tag, valores in geo.items():
                et.append((tag, 3 if tag == 34735 else 12, valores))
        self.directorios.append(sorted(et))

    def cerrar(self):
        tam = {3: 2, 4: 4, 7: 1, 12: 8, 16: 8}
        fmt = {3: "H", 4: "I", 12: "d", 16: "Q"}
        anterior = None
        for et in self.directorios:
            # valores largos primero, luego el directorio
            valores = {}
            for tag, tipo, v in et:
                n = len(v) * tam[tipo]
                if n > (8 if self.grande else 4):
                    valores[tag] = self.f.tell()
                    self.f.write(bytes(v) if tipo == 7 else struct.pack(self.e + fmt[tipo] * len(v), *v))
                    if self.f.tell() % 2:
                        self.f.write(b"\0")
            inicio = self.f.tell()
            if anterior is not None:
                self.f.seek(anterior)
                self.f.write(struct.pack(self.e + ("Q" if self.grande else "I"), inicio))
                self.f.seek(inicio)
            else:
                self.f.seek(8 if self.grande else 4)
                self.f.write(struct.pack(self.e + ("Q" if self.grande else "I"), inicio))
                self.f.seek(inicio)
            self.f.write(struct.pack(self.e + ("Q" if self.grande else "H"), len(et)))
            for tag, tipo, v in et:
                n = len(v) * tam[tipo]
                cuenta = struct.pack(self.e + ("Q" if self.grande else "I"), len(v))
                self.f.write(struct.pack(self.e + "HH", tag, tipo) + cuenta)
                ancho_campo = 8 if self.grande else 4
                if n > ancho_campo:
                    self.f.write(struct.pack(self.e + ("Q" if self.grande else "I"), valores[tag]))
                else:
                    crudo = bytes(v) if tipo == 7 else struct.pack(self.e + fmt[tipo] * len(v), *v)
                    self.f.write(crudo.ljust(ancho_campo, b"\0"))
            anterior = self.f.tell()
            self.f.write(struct.pack(self.e + ("Q" if self.grande else "I"), 0))
        self.f.close()


def promedio(nivel_completo, factor):
    """Función de bloques de una vista general (promedio de factor × factor del patrón)."""
    def bloques(x0, y0, w, h):
        b = nivel_completo(x0 * factor, y0 * factor, w * factor, h * factor).astype(np.uint32)
        b = b.reshape(h, factor, w, factor, b.shape[2]).mean(axis=(1, 3))
        return np.round(b).astype(np.uint8)
    return bloques


def geo_utm(x_esquina, y_esquina, pixel, epsg=32614, puntual=False):
    """Etiquetas GeoTIFF: pixel cuadrado, esquina superior izquierda (o centro, puntual) en (x, y), EPSG."""
    return {33550: [pixel, pixel, 0.0], 33922: [0.0, 0.0, 0.0, x_esquina, y_esquina, 0.0],
            34735: [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 2 if puntual else 1, 3072, 0, 1, epsg]}


def tiff(ruta: Path, ancho: int, alto: int, *, mosaico=None, filas_tira=None, compresion="none", spp=3, alfa=False, planar=1,
         predictor=1, grande=False, mm=False, vistas=0, geo=None, bits=8) -> Path:
    canales = spp + (1 if alfa else 0)
    completo = lambda x0, y0, w, h: patron(x0, y0, w, h, spp, alfa)  # noqa: E731
    e = EscritorTiff(ruta, grande=grande, mm=mm)
    e.nivel(ancho, alto, completo, mosaico=mosaico, filas_tira=filas_tira, spp=canales, alfa=alfa, compresion=compresion, planar=planar,
            predictor=predictor, geo=geo, bits=bits)
    for k in range(1, vistas + 1):
        f = 2 ** k
        e.nivel(ancho // f, alto // f, promedio(completo, f), mosaico=mosaico, filas_tira=filas_tira, spp=canales, alfa=alfa,
                compresion=compresion, planar=planar, predictor=predictor, reducida=True, bits=bits)
    e.cerrar()
    return ruta


def mundo(ruta: Path, a, d, b, e, c, f) -> Path:
    ruta.write_text("\r\n".join(f"{v:.10f}" for v in (a, d, b, e, c, f)) + "\r\n", encoding="ascii")
    return ruta


def grande(carpeta: Path, gb: float, mosaico: int | None, tiras: int | None) -> Path:
    """Un BigTIFF RGB de `gb` GB sin comprimir y sin vistas generales (lo peor: la pirámide se arma en la computadora)."""
    lado = int(math.sqrt(gb * 1e9 / 3))
    ancho, alto = lado - lado % 512, lado - lado % 512
    nombre = f"orto_{'mosaico' if mosaico else 'tiras'}_{ancho}x{alto}.tif"
    ruta = tiff(carpeta / nombre, ancho, alto, mosaico=mosaico, filas_tira=tiras, grande=True,
                geo=geo_utm(389000.0, 3169000.0 + alto * 0.05, 0.05))
    return ruta


def pointcab(carpeta: Path, imagenes: int = 320, lado: int = 1024) -> Path:
    """Un juego «tipo PointCab» sintético: un DXF con `imagenes` IMAGE (fachadas y cortes en una cuadrícula, cada uno una imagen) que
    apuntan a PNG y JPG en carpetas por vista, con rutas absolutas de Windows como las deja la exportación, más una referencia externa
    (un DXF con el marco y los ejes) adjunta."""
    import ezdxf
    from PIL import Image, ImageDraw

    carpeta.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2018", setup=False)
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    vistas = ["Fachada_Norte", "Fachada_Sur", "Fachada_Este", "Fachada_Oeste", "Corte_A", "Corte_B", "Planta_N0", "Planta_N1"]
    por_vista = math.ceil(imagenes / len(vistas))
    n = 0
    for v, vista in enumerate(vistas):
        (carpeta / "Imagenes" / vista).mkdir(parents=True, exist_ok=True)
        for k in range(por_vista):
            if n >= imagenes:
                break
            ext = "jpg" if k % 3 else "png"
            nombre = f"{vista}_{k + 1:03d}.{ext}"
            ruta = carpeta / "Imagenes" / vista / nombre
            w, h = lado, lado * 3 // 4
            im = Image.fromarray(patron(k * w, v * h, w, h))
            d = ImageDraw.Draw(im)
            d.rectangle([4, 4, w - 5, h - 5], outline=(255, 255, 255), width=6)
            d.text((24, 24), f"{vista} {k + 1}", fill=(255, 255, 255))
            im.save(ruta, quality=88) if ext == "jpg" else im.save(ruta, optimize=False, compress_level=3)
            ancho_m = 4.0
            x, y = 389000.0 + k * (ancho_m + 0.2), 3169000.0 + v * (ancho_m * 0.75 + 0.5)
            ref = "C:\\PointCab\\Proyecto_Demo\\Imagenes\\" + vista + "\\" + nombre
            definicion = doc.add_image_def(filename=ref, size_in_pixel=(w, h))
            msp.add_image(definicion, insert=(x, y), size_in_units=(ancho_m, ancho_m * h / w), rotation=0)
            n += 1
    # Marco y ejes como referencia externa.
    marco = ezdxf.new("R2018", setup=False)
    mm = marco.modelspace()
    ancho_total = por_vista * 4.2
    alto_total = len(vistas) * 3.5
    mm.add_lwpolyline([(389000 - 1, 3169000 - 1), (389000 + ancho_total + 1, 3169000 - 1), (389000 + ancho_total + 1, 3169000 + alto_total + 1),
                       (389000 - 1, 3169000 + alto_total + 1)], close=True)
    (carpeta / "Referencias").mkdir(exist_ok=True)
    marco.saveas(carpeta / "Referencias" / "Marco_y_ejes.dxf")
    doc.add_xref_def("C:\\PointCab\\Proyecto_Demo\\Referencias\\Marco_y_ejes.dxf", "Marco_y_ejes")
    msp.add_blockref("Marco_y_ejes", (0, 0))
    salida = carpeta / "Proyecto_Demo_fachadas.dxf"
    doc.saveas(salida)
    return salida


def principal():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="que", required=True)
    g = sub.add_parser("grande")
    g.add_argument("carpeta", type=Path)
    g.add_argument("--gb", type=float, default=3.2)
    g.add_argument("--mosaico", type=int)
    g.add_argument("--tiras", type=int)
    pc = sub.add_parser("pointcab")
    pc.add_argument("carpeta", type=Path)
    pc.add_argument("--imagenes", type=int, default=320)
    a = p.parse_args()
    if a.que == "grande":
        print(grande(a.carpeta, a.gb, a.mosaico, a.tiras))
    else:
        print(pointcab(a.carpeta, a.imagenes))


if __name__ == "__main__":
    principal()
