"""PPK local con RTKLIB. No modifica los insumos; alturas elipsoidales.

La concordancia adelante/atrás y la covarianza son controles internos,
no sustituyen puntos de comprobación independientes.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timedelta
from bisect import bisect_left
import collections
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from zipfile import ZipFile

from .app_storage import cache_dir

INSTALL_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ROOT = INSTALL_ROOT / "assets" / "ppk_runtime"
RUNTIME_VERSION = "rtklib-2.5.1_exiftool-13.59"
_RUNTIME_LOCK = threading.Lock()
GPS_EPOCH = datetime(1980, 1, 6)
ICHI = (28 + 38/60 + 50.05040/3600, -(106 + 3/60 + 58.01000/3600), 1405.717)
ICHI_SOURCE = 'https://www.inegi.org.mx/contenidos/temas/geodesia_activa/doc/cale2026_itrf2008.pdf'
Q_LABEL = {1: 'FIX', 2: 'FLOAT', 3: 'SBAS', 4: 'DGPS', 5: 'SINGLE', 6: 'PPP'}
A = 6378137.0
E2 = 6.6943799901413165e-3


class PPKError(Exception):
    pass


class Cancelled(PPKError):
    pass


@dataclass
class Settings:
    photos: str = ''
    rover: str = ''
    base: str = ''
    nav: list[str] | None = None
    mrk: str = ''
    output: str = ''
    base_mode: str = 'header'
    base_lat: float = 0.0
    base_lon: float = 0.0
    base_h: float = 0.0
    antenna_height: float | None = None
    antenna_type: str = ''
    iono: str = 'auto'
    export_provisional: bool = False
    lever: str = 'auto'
    time_offset: float = -6.0
    max_gap: float = 0.5
    max_fb_h: float = 0.10
    max_fb_v: float = 0.20


@dataclass(frozen=True)
class Event:
    index: int
    week: int
    sow: float
    north_mm: float
    east_mm: float
    down_mm: float

    @property
    def t(self):
        return self.week * 604800 + self.sow


@dataclass(frozen=True)
class Solution:
    t: float
    lat: float
    lon: float
    h: float
    q: int
    ns: int
    sn: float
    se: float
    su: float
    age: float
    ratio: float


def llh_xyz(lat, lon, h):
    p, l = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1 - E2 * math.sin(p)**2)
    return ((n+h)*math.cos(p)*math.cos(l), (n+h)*math.cos(p)*math.sin(l), (n*(1-E2)+h)*math.sin(p))


def xyz_llh(x, y, z):
    rho = math.hypot(x, y)
    if rho < 1e-6:
        raise PPKError('Coordenadas ECEF de base inválidas o en el polo.')
    p = math.atan2(z, rho*(1-E2))
    for _ in range(12):
        n = A / math.sqrt(1-E2*math.sin(p)**2)
        p = math.atan2(z + E2*n*math.sin(p), rho)
    h = rho/math.cos(p)-A/math.sqrt(1-E2*math.sin(p)**2)
    return math.degrees(p), math.degrees(math.atan2(y, x)), h


def ned_xyz(lat, lon, north, east, down):
    p, l = math.radians(lat), math.radians(lon)
    return (-math.sin(p)*math.cos(l)*north-math.sin(l)*east-math.cos(p)*math.cos(l)*down,
            -math.sin(p)*math.sin(l)*north+math.cos(l)*east-math.cos(p)*math.sin(l)*down,
            math.cos(p)*north-math.sin(p)*down)


def separation(a, b):
    dx, dy, dz = [v-u for u, v in zip(llh_xyz(a.lat, a.lon, a.h), llh_xyz(b.lat, b.lon, b.h))]
    p, l = math.radians(a.lat), math.radians(a.lon)
    east = -math.sin(l)*dx+math.cos(l)*dy
    north = -math.sin(p)*math.cos(l)*dx-math.sin(p)*math.sin(l)*dy+math.cos(p)*dz
    up = math.cos(p)*math.cos(l)*dx+math.cos(p)*math.sin(l)*dy+math.sin(p)*dz
    return math.hypot(north, east), abs(up)


def gps_datetime(t):
    return GPS_EPOCH + timedelta(seconds=t)


def gps_utc(t):
    # Fechas UTC efectivas del incremento GPS-UTC (IERS, hasta 2017).
    dates = [(1981,7,1),(1982,7,1),(1983,7,1),(1985,7,1),(1988,1,1),
             (1990,1,1),(1991,1,1),(1992,7,1),(1993,7,1),(1994,7,1),
             (1996,1,1),(1997,7,1),(1999,1,1),(2006,1,1),(2009,1,1),
             (2012,7,1),(2015,7,1),(2017,1,1)]
    g = gps_datetime(t)
    offset = sum(g >= datetime(*d)+timedelta(seconds=i+1) for i,d in enumerate(dates))
    return g-timedelta(seconds=offset)


def parse_mrk(path):
    events = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        m = re.match(r'^\s*(\d+)\s+([\d.]+)\s+\[(\d+)\]\s+([-+\d.]+),N\s+([-+\d.]+),E\s+([-+\d.]+),[VD]', line)
        if not m:
            raise PPKError(f'MRK: formato no reconocido en la línea {number}.')
        i, sow, week, n, e, d = m.groups()
        event = Event(int(i), int(week), float(sow), float(n), float(e), float(d))
        if not 0 <= event.sow < 604800 or not 0 < event.week < 6000:
            raise PPKError(f'MRK: semana/segundos GPS inválidos, línea {number}.')
        if events and (event.t <= events[-1].t or event.index <= events[-1].index):
            raise PPKError('MRK: disparos repetidos o fuera de orden.')
        if max(abs(event.north_mm),abs(event.east_mm),abs(event.down_mm)) > 10000:
            raise PPKError('MRK: desplazamiento antena/cámara fuera de rango (milímetros).')
        events.append(event)
    if not events:
        raise PPKError('El MRK no contiene disparos.')
    return events


def rinex_info(path):
    """Lee encabezado y épocas reales. No confía en TIME OF LAST OBS."""
    header, epochs, in_header = [], [], True
    with Path(path).open(encoding='ascii', errors='strict') as f:
        for line in f:
            if in_header:
                header.append(line.rstrip('\n'))
                if 'END OF HEADER' in line:
                    in_header = False
                elif len(header) > 1000:
                    raise PPKError('Encabezado RINEX no válido.')
                continue
            fields = None
            if line.startswith('>'):
                p = line[1:].split()
                if len(p) >= 8 and p[6] in ('0','1'):
                    fields = p[:6]
            elif len(line) >= 32 and re.match(r'^ [ 0-9]\d [ 0-9]\d [ 0-9]\d ', line):
                try:
                    if int(line[28:29]) in (0,1):
                        fields = line[:26].split()[:6]
                        fields[0] = str(int(fields[0]) + (2000 if int(fields[0]) < 80 else 1900))
                except (ValueError, IndexError):
                    pass
            if fields and len(fields) == 6:
                try:
                    dt = datetime(*[int(x) for x in fields[:5]]) + timedelta(seconds=float(fields[5]))
                    epochs.append((dt-GPS_EPOCH).total_seconds())
                except ValueError:
                    raise PPKError(f'Época no válida en {Path(path).name}.')
    if in_header or not header or 'RINEX VERSION / TYPE' not in header[0]:
        raise PPKError(f'{Path(path).name} no es un RINEX de texto sin comprimir.')
    values = {}
    for line in header:
        label = line[60:].strip()
        values[label] = line[:60]
    if 'OBSERVATION DATA' not in header[0] or not epochs:
        raise PPKError(f'{Path(path).name} no contiene observaciones RINEX compatibles.')
    time_sys = values.get('TIME OF FIRST OBS','').ljust(60)[48:51].strip()
    if time_sys not in ('', 'GPS'):
        raise PPKError(f'Tiempo {time_sys} no soportado: convierte las observaciones a GPS antes de procesar.')
    epochs = sorted(set(epochs))
    xyz = [float(x) for x in values.get('APPROX POSITION XYZ','0 0 0').split()[:3]]
    delta = [float(x) for x in values.get('ANTENNA: DELTA H/E/N','0 0 0').split()[:3]]
    if len(xyz) != 3 or len(delta) != 3:
        raise PPKError('Coordenadas/altura de antena incompletas en el RINEX.')
    diffs = sorted(b-a for a,b in zip(epochs,epochs[1:]) if b>a)
    return {'marker':values.get('MARKER NAME','').strip(), 'xyz':xyz, 'delta_hen':delta,
            'antenna':values.get('ANT # / TYPE','').ljust(40)[20:40].strip(),
            'start':epochs[0], 'end':epochs[-1], 'epochs':epochs,
            'interval':diffs[len(diffs)//2] if diffs else 0, 'header':header}


def normalize_nav(source, dest):
    """Corrige sólo la variante INEGI Galileo 2.11 con cuerpo ya RINEX 3."""
    lines = Path(source).read_text(encoding='ascii').splitlines()
    if not lines or 'RINEX VERSION / TYPE' not in lines[0]:
        raise PPKError(f'Navegación inválida: {Path(source).name}. Descomprime los RINEX primero.')
    changed = False
    if float(lines[0][:9]) < 3 and lines[0][20:21] == 'E':
        end = next((i for i,l in enumerate(lines) if 'END OF HEADER' in l), None)
        if end is None:
            raise PPKError('Navegación Galileo sin fin de encabezado.')
        body = lines[end+1:]
        if not body or len(body) % 8 or any(not re.match(r'^E[ 0-9]\d \d{4} ', body[i]) for i in range(0,len(body),8)):
            raise PPKError('Navegación Galileo 2.11 no compatible; se necesita RINEX 3 válido.')
        for i in range(end+1,len(lines),8):
            lines[i]=f'E{int(lines[i][1:3]):02d}'+lines[i][3:]
        lines[0] = f"{'     3.03':20s}{'N: GNSS NAV DATA':20s}{'E: GALILEO':20s}RINEX VERSION / TYPE"
        changed = True
    Path(dest).write_text('\n'.join(lines)+'\n', encoding='ascii')
    return changed


def parse_pos(path):
    values = {}
    text = Path(path).read_text(encoding='ascii')
    if 'GPST' not in text or 'latitude(deg)' not in text:
        raise PPKError('La solución no usa semana/segundos GPS y latitud/longitud decimal.')
    for line in text.splitlines():
        if not line.strip() or line.startswith('%'):
            continue
        p = line.split()
        if len(p) < 15:
            raise PPKError('Fila de solución RTKLIB incompleta.')
        try:
            n = [float(x) for x in p]
        except ValueError as exc:
            raise PPKError('Solución RTKLIB inválida.') from exc
        if not all(math.isfinite(x) for x in n):
            continue
        s = Solution(n[0]*604800+n[1], n[2],n[3],n[4],int(n[5]),int(n[6]),n[7],n[8],n[9],n[13],n[14])
        if not (-90<=s.lat<=90 and -180<=s.lon<=180 and s.q in Q_LABEL):
            continue
        # RTKLIB puede escribir una inicialización y otra solución en la misma época.
        old = values.get(s.t)
        if old is None or (s.q != 5 and s.sn*s.sn+s.se*s.se+s.su*s.su <= old.sn*old.sn+old.se*old.se+old.su*old.su):
            values[s.t] = s
    if len(values)<2:
        raise PPKError('RTKLIB no produjo suficientes posiciones. Consulta el registro del cálculo.')
    return sorted(values.values(), key=lambda x:x.t)


def interpolate(rows, times, t, max_gap=0.5):
    j = bisect_left(times,t)
    if j < len(rows) and abs(times[j]-t)<1e-6:
        return rows[j]
    if j == 0 or j == len(rows):
        raise PPKError('Disparo fuera del intervalo resuelto; no se extrapola.')
    a,b = rows[j-1:j+1]
    if b.t-a.t > max_gap+1e-6:
        raise PPKError(f'Hueco de {b.t-a.t:.2f} s en la trayectoria.')
    f = (t-a.t)/(b.t-a.t)
    xyz = tuple(x+(y-x)*f for x,y in zip(llh_xyz(a.lat,a.lon,a.h),llh_xyz(b.lat,b.lon,b.h)))
    lat,lon,h = xyz_llh(*xyz)
    q = a.q if a.q == b.q else (2 if {a.q,b.q} <= {1,2} else max(a.q,b.q))
    return Solution(t,lat,lon,h,q,min(a.ns,b.ns),max(a.sn,b.sn),max(a.se,b.se),max(a.su,b.su),max(abs(a.age),abs(b.age)),min(a.ratio,b.ratio))


def p4_nominal_ned(meta):
    """Modelo nominal P4 RTK: FRD (0.036, 0, 0.192) m, Rz Ry Rx.

    Es una aproximación con actitud EXIF; no reemplaza los offsets TimeSync MRK.
    """
    roll,pitch,yaw = [math.radians(float(meta[f'XMP-drone-dji:Flight{k}Degree'])) for k in ('Roll','Pitch','Yaw')]
    x,y,z = 0.036, -math.sin(roll)*0.192, math.cos(roll)*0.192
    x,z = math.cos(pitch)*x+math.sin(pitch)*z, -math.sin(pitch)*x+math.cos(pitch)*z
    return math.cos(yaw)*x-math.sin(yaw)*y, math.sin(yaw)*x+math.cos(yaw)*y, z


def camera_position(sol,event,meta,mode):
    if mode == 'antenna':
        return sol.lat,sol.lon,sol.h,'ANTENA_SIN_COMPENSAR',0.20
    if any(abs(v)>0 for v in (event.north_mm,event.east_mm,event.down_mm)):
        n,e,d = event.north_mm/1000,event.east_mm/1000,event.down_mm/1000
        label, floor = 'MRK_NED',0.0
    elif mode == 'auto' and meta.get('IFD0:Model') == 'FC6310R' and all(f'XMP-drone-dji:Flight{k}Degree' in meta for k in ('Roll','Pitch','Yaw')):
        n,e,d = p4_nominal_ned(meta)
        label,floor = 'P4_RTK_NOMINAL_NO_CALIBRADO',0.20
    else:
        raise PPKError('MRK sin compensación antena/cámara; selecciona la salida de antena o aporta offsets válidos.')
    xyz = [v+dv for v,dv in zip(llh_xyz(sol.lat,sol.lon,sol.h),ned_xyz(sol.lat,sol.lon,n,e,d))]
    return *xyz_llh(*xyz),label,floor


def _extract_file(archive: ZipFile, member: str, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, destination.open('wb') as target:
        shutil.copyfileobj(source, target)


def _extract_archive_safely(archive: ZipFile, destination: Path):
    root = destination.resolve()
    for item in archive.infolist():
        target = (destination / item.filename).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PPKError(f'Archivo inseguro en el motor PPK: {item.filename}') from exc
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            _extract_file(archive, item.filename, target)


def tool_paths():
    """Install the bundled engines once in the managed cache and return them."""
    runtime = cache_dir('ppk_runtime') / RUNTIME_VERSION
    marker = runtime / '.runtime_ready'
    rtk = runtime / 'rtklib' / 'rnx2rtkp.exe'
    atx = runtime / 'rtklib' / 'igs20_2353.atx'
    exif = runtime / 'exiftool' / 'ExifTool.exe'
    with _RUNTIME_LOCK:
        if not marker.exists() or not all(path.is_file() for path in (rtk, exif, atx)):
            exif_archive = ROOT / 'exiftool.zip'
            rtk_archive = ROOT / 'RTKLIB_EX_2.5.1.zip'
            if not exif_archive.is_file() or not rtk_archive.is_file():
                raise PPKError(f'No se encontraron los motores comprimidos en {ROOT}.')
            runtime.mkdir(parents=True, exist_ok=True)
            with ZipFile(exif_archive) as archive:
                _extract_archive_safely(archive, runtime / 'exiftool')
            with ZipFile(rtk_archive) as archive:
                _extract_file(archive, 'RTKLIB_EX_2.5.1/rnx2rtkp.exe', rtk)
                _extract_file(archive, 'RTKLIB_EX_2.5.1/igs20_2353.atx', atx)
            if not all(path.is_file() for path in (rtk, exif, atx)):
                raise PPKError('La instalación local de RTKLIB o ExifTool quedó incompleta.')
            marker.write_text(RUNTIME_VERSION, encoding='ascii')
    return rtk.resolve(), exif.resolve(), atx.resolve()


def run_cmd(args, cwd=None, logfile=None, cancel=None):
    env = os.environ | {'LC_ALL':'C','LC_CTYPE':'C','LANG':'C'}
    with tempfile.TemporaryFile() as stream:
        p = subprocess.Popen([str(a) for a in args], cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                             env=env, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        while p.poll() is None:
            if cancel is not None and cancel.wait(0.15):
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait()
                raise Cancelled('Proceso cancelado. Los originales se conservan.')
            if cancel is None:
                time.sleep(0.1)
        stream.seek(0)
        output=stream.read()
    if logfile:
        Path(logfile).write_bytes(output)
    if p.returncode:
        raise PPKError(f'{Path(args[0]).name} terminó con error {p.returncode}:\n{output.decode(errors="replace")[-2000:]}')
    return output


def read_metadata(exif, photos, work, cancel=None):
    argfile=work/'leer_exif.args'
    args=['-j','-n','-G1','-charset','filename=UTF8','-Model','-DateTimeOriginal',
          '-XMP-drone-dji:all','-Composite:GPSLatitude','-Composite:GPSLongitude','-Composite:GPSAltitude']+[str(p) for p in photos]
    argfile.write_text('\n'.join(args),encoding='utf-8')
    # stderr se mantiene separado para que las advertencias no invaliden el JSON.
    env=os.environ|{'LC_ALL':'C','LC_CTYPE':'C','LANG':'C'}
    p=subprocess.Popen([str(exif),'-@',str(argfile)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    while True:
        try:
            out,err=p.communicate(timeout=0.25); break
        except subprocess.TimeoutExpired:
            if cancel and cancel.is_set():
                p.kill(); p.communicate(); raise Cancelled('Lectura cancelada.')
    (work/'lectura_exif.log').write_bytes(err)
    if p.returncode:
        raise PPKError('No se pudieron leer los metadatos: '+err.decode(errors='replace')[-1000:])
    records=json.loads(out)
    return {Path(r['SourceFile']).name:r for r in records}


def match_photos(events, photos, metadata, mrk_path, tz_hours):
    prefix=re.sub(r'_Timestamp$', '', Path(mrk_path).stem, flags=re.I)
    by_name={p.stem.casefold():p for p in photos}
    pairs, used = [], set()
    for ev in events:
        expected=f'{prefix}_{ev.index:04d}'.casefold()
        candidates=[]
        for p in photos:
            pd=str(metadata[p.name].get('XMP-drone-dji:PhotoDiff',''))
            mt=re.search(r'(\d{14})$',pd)
            if mt:
                stamp=datetime.strptime(mt[1],'%Y%m%d%H%M%S')
                delta=(gps_datetime(ev.t)-stamp).total_seconds()
                if -0.05 <= delta < 1.05:
                    candidates.append(p)
        target=by_name.get(expected)
        if target and candidates and target not in candidates:
            raise PPKError(f'La foto {target.name} no coincide con la hora de su disparo MRK.')
        if target is None:
            if len(candidates)==1:
                target=candidates[0]
            elif len(candidates)>1:
                pairs.append((ev,None,'Varias fotos tienen la misma marca PhotoDiff; asociación ambigua.')); continue
        if target is None:
            pairs.append((ev,None,'No se encontró la foto correspondiente al disparo.')); continue
        if target in used:
            raise PPKError('La asociación intentó usar una foto para dos disparos.')
        meta=metadata[target.name]
        stamp=meta.get('ExifIFD:DateTimeOriginal')
        pd=meta.get('XMP-drone-dji:PhotoDiff')
        if pd:
            if target not in candidates:
                raise PPKError(f'PhotoDiff incompatible con MRK: {target.name}.')
            method='PhotoDiff_GPS_y_nombre' if expected==target.stem.casefold() else 'PhotoDiff_GPS'
        elif stamp:
            dt=datetime.strptime(stamp[:19],'%Y:%m:%d %H:%M:%S')
            local=gps_utc(ev.t)+timedelta(hours=tz_hours)
            if abs((dt-local).total_seconds()) > 1.2:
                raise PPKError(f'Hora EXIF incompatible: {target.name}. Revisa el huso horario de la cámara.')
            method='Nombre_y_EXIF_segundos'
        else:
            raise PPKError(f'{target.name}: no hay marca temporal verificable para asociarla al disparo.')
        pairs.append((ev,target,method)); used.add(target)
    return pairs,[p for p in photos if p not in used]


def choose_base(settings,info):
    antenna=settings.antenna_type.strip() or info['antenna']
    height=info['delta_hen'][0] if settings.antenna_height is None else settings.antenna_height
    if settings.base_mode=='ichi':
        if info['marker'].upper()!='ICHI':
            raise PPKError('El perfil ICHI sólo se puede usar con la estación ICHI.')
        llh=ICHI; height=0.188; antenna='TRM115000.00    NONE'
        frame='Vinculado a ICHI ITRF2008 época 2010.0; sin propagación de velocidades ni transformación rigurosa de época.'
    elif settings.base_mode=='manual':
        llh=(settings.base_lat,settings.base_lon,settings.base_h)
        if not (-90<llh[0]<90 and -180<=llh[1]<=180 and -1000<llh[2]<10000):
            raise PPKError('Coordenadas manuales de base inválidas.')
        frame='Marco de las coordenadas manuales de base; especificar datum y época externamente.'
    else:
        llh=xyz_llh(*info['xyz'])
        frame='Marco/época no certificados: coordenadas APPROX POSITION XYZ del RINEX base.'
    if not math.isfinite(height) or not 0<=height<100:
        raise PPKError('Altura vertical de antena inválida.')
    return llh,height,antenna,frame


def write_config(path,settings,base_llh,height,antenna,atx,baseline,direction,base_info):
    iono = ('est-stec' if baseline>10000 else 'brdc') if settings.iono=='auto' else settings.iono
    options={
        'pos1-posmode':'kinematic','pos1-frequency':'l1+l2','pos1-soltype':direction,
        'pos1-elmask':15,'pos1-dynamics':'on','pos1-ionoopt':iono,'pos1-tropopt':'saas',
        'pos1-navsys':45,'pos2-armode':'continuous','pos2-gloarmode':'off',
        'pos2-arthres':3,'pos2-arthresmin':3,'pos2-arthresmax':3,'pos2-arfilter':'on',
        'pos2-arlockcnt':5,'pos2-maxage':30,'pos2-slipthres':0.05,
        'out-solformat':'llh','out-outhead':'on','out-outopt':'on','out-timesys':'gpst',
        'out-timeform':'tow','out-timendec':6,'out-height':'ellipsoidal',
        'out-outsingle':'off','out-outstat':'residual','ant2-postype':'llh',
        'ant2-pos1':base_llh[0],'ant2-pos2':base_llh[1],'ant2-pos3':base_llh[2],
        'ant2-anttype':antenna,'ant2-antdelu':height,
        'ant2-antdele':base_info['delta_hen'][1],'ant2-antdeln':base_info['delta_hen'][2],
        'misc-timeinterp':'on','file-rcvantfile':str(atx),
        'stats-eratio1':100,'stats-eratio2':100,'stats-errphase':0.003,'stats-errphaseel':0.003,
    }
    path.write_text('\n'.join(f'{k}={v}' for k,v in options.items())+'\n',encoding='ascii')
    return iono


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024*1024): h.update(block)
    return h.hexdigest()


def jpeg_payload_hash(path):
    """Hash de todos los segmentos JPEG salvo APP/COM. Comprueba que no hubo recompresión."""
    data=Path(path).read_bytes()
    if data[:2]!=b'\xff\xd8': raise PPKError('Imagen JPEG inválida.')
    h=hashlib.sha256(); h.update(data[:2]); p=2
    while p<len(data):
        start=p
        if data[p]!=255: raise PPKError('Estructura JPEG inválida.')
        while data[p]==255: p+=1
        marker=data[p]; p+=1
        if marker in (0xD9,0xDA):
            h.update(data[start:]); return h.hexdigest()
        if marker in (0xD8,0x01) or 0xD0<=marker<=0xD7:
            h.update(data[start:p]); continue
        size=int.from_bytes(data[p:p+2],'big')
        if size<2 or p+size>len(data): raise PPKError('Segmento JPEG inválido.')
        end=p+size
        if not (0xE0<=marker<=0xEF or marker==0xFE): h.update(data[start:end])
        p=end
    raise PPKError('JPEG sin imagen comprimida.')


def export_photos(rows,exif,result,work,cancel,log):
    exports=[]; args=['-charset','filename=UTF8']
    for row in rows:
        if not row.get('exportar'): continue
        if cancel.is_set(): raise Cancelled('Exportación cancelada.')
        folder=result/('Fotos_PPK_FIX' if row['estado']=='FIX_CONCORDANTE' else 'Fotos_PPK_PROVISIONALES')
        folder.mkdir(exist_ok=True)
        src=Path(row['original']); dest=folder/src.name
        if dest.exists(): raise PPKError('Ya existe una foto en el destino.')
        shutil.copy2(src,dest)
        lat,lon,h=row['latitud'],row['longitud'],row['altura_elipsoidal_m']
        args.extend(['-charset','filename=UTF8','-overwrite_original','-P',f'-EXIF:GPSLatitude={abs(lat):.10f}',
                     f'-EXIF:GPSLatitudeRef={"N" if lat>=0 else "S"}',f'-EXIF:GPSLongitude={abs(lon):.10f}',
                     f'-EXIF:GPSLongitudeRef={"E" if lon>=0 else "W"}',f'-EXIF:GPSAltitude={abs(h):.4f}',
                     f'-EXIF:GPSAltitudeRef={0 if h>=0 else 1}',
                     f'-XMP-drone-dji:GPSLatitude={lat:.10f}', f'-XMP-drone-dji:GPSLongitude={lon:.10f}',
                     f'-XMP-drone-dji:GPSLongtitude={lon:.10f}',f'-XMP-drone-dji:AbsoluteAltitude={h:.4f}',
                     f'-XMP-drone-dji:RtkFlag={50 if row["estado"]=="FIX_CONCORDANTE" else 0}',
                     f'-XMP-drone-dji:RtkStdLat={row["incertidumbre_n_m"]:.5f}',
                     f'-XMP-drone-dji:RtkStdLon={row["incertidumbre_e_m"]:.5f}',
                     f'-XMP-drone-dji:RtkStdHgt={row["incertidumbre_h_m"]:.5f}',
                     '-EXIF:GPSMapDatum=',
                     f'-XMP-dc:Description=PPK RTKLIB; {row["estado"]}; altura elipsoidal; {row["compensacion"]}; consultar informe y CSV.',
                     str(dest),'-execute'])
        row['exportada']=str(dest)
        exports.append((src,dest,row))
    if not exports: return
    argsfile=work/'escribir_exif.args'; argsfile.write_text('\n'.join(args)+'\n',encoding='utf-8')
    log(f'Escribiendo metadatos en {len(exports)} copias...')
    output=run_cmd([exif,'-@',argsfile],logfile=work/'escritura_exif.log',cancel=cancel)
    if b'Error:' in output or b'Warning: Sorry' in output:
        raise PPKError('ExifTool informó problemas al escribir; consulta escritura_exif.log.')
    verified=read_metadata(exif,[d for _,d,_ in exports],work,cancel)
    for src,dest,row in exports:
        meta=verified[dest.name]
        checks=[('Composite:GPSLatitude','latitud',2e-8),('Composite:GPSLongitude','longitud',2e-8),
                ('Composite:GPSAltitude','altura_elipsoidal_m',0.002),
                ('XMP-drone-dji:GPSLatitude','latitud',2e-8),('XMP-drone-dji:GPSLongtitude','longitud',2e-8),
                ('XMP-drone-dji:AbsoluteAltitude','altura_elipsoidal_m',0.002)]
        for key,field,tol in checks:
            if key not in meta or abs(float(meta[key])-row[field])>tol:
                raise PPKError(f'Falló la verificación {key} de {dest.name}.')
        if jpeg_payload_hash(src)!=jpeg_payload_hash(dest):
            raise PPKError(f'Cambió el contenido JPEG de {dest.name}.')
        if sha256(src)!=row['sha256_original']:
            raise PPKError(f'El original cambió durante el procesamiento: {src.name}.')
        row['verificacion']='EXIF_XMP_y_JPEG_OK'
        row['sha256_exportada']=sha256(dest)
    log('Verificado: coordenadas EXIF/XMP coinciden y los JPEG no se recomprimieron.')


def process(settings,log=lambda s:None,cancel=None):
    cancel=cancel or threading.Event()
    rtk,exif,atx=tool_paths()
    for label,value in [('Fotos',settings.photos),('Rover',settings.rover),('Base',settings.base),('MRK',settings.mrk)]:
        if not value or not Path(value).exists(): raise PPKError(f'{label}: ruta no encontrada.')
    if not settings.nav or not 1<=len(settings.nav)<=12 or any(not Path(p).is_file() for p in settings.nav):
        raise PPKError('Selecciona entre 1 y 12 archivos de navegación existentes.')
    if Path(settings.rover).resolve()==Path(settings.base).resolve():
        raise PPKError('Rover y base deben ser archivos distintos.')
    if not settings.output: raise PPKError('Selecciona la carpeta de salida.')
    if not (0<settings.max_gap<=2 and 0<settings.max_fb_h<=10 and 0<settings.max_fb_v<=10):
        raise PPKError('Tolerancias de tiempo/concordancia fuera de rango.')
    log('Leyendo tiempos reales, observaciones y disparos...')
    events=parse_mrk(settings.mrk)
    rover,base=rinex_info(settings.rover),rinex_info(settings.base)
    if max(rover['start'],base['start'])>=min(rover['end'],base['end']):
        raise PPKError('No hay coincidencia temporal entre base y rover.')
    if base['interval']>30: raise PPKError('Base con intervalo superior a 30 s: se necesitan observaciones más frecuentes.')
    if not any(rover['start']<=e.t<=rover['end'] for e in events):
        raise PPKError('El MRK corresponde a otro intervalo/semana GPS.')
    photos=sorted(p.resolve() for p in Path(settings.photos).iterdir() if p.suffix.lower() in ('.jpg','.jpeg'))
    if not photos: raise PPKError('No hay fotos JPG/JPEG en la carpeta seleccionada.')
    base_llh,height,antenna,frame=choose_base(settings,base)
    baseline=math.dist(llh_xyz(*base_llh),rover['xyz'])
    if not 1e6<math.sqrt(sum(x*x for x in rover['xyz']))<8e6:
        raise PPKError('El rover no tiene APPROX POSITION XYZ válida para evaluar la distancia a la base.')
    log(f'{len(photos)} fotos; {len(events)} disparos; base {base["marker"]}; distancia aproximada {baseline/1000:.1f} km.')
    result=Path(settings.output).resolve()/('PPK_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    result.mkdir(parents=True,exist_ok=False)
    work=result/'Calculo'; work.mkdir()
    (result/'PROCESO_EN_CURSO.txt').write_text('No utilizar resultados hasta que exista INFORME.txt con estado COMPLETADO.\n',encoding='utf-8')
    try:
        metadata=read_metadata(exif,photos,work,cancel)
        (work/'metadatos_originales.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
        pairs,unused=match_photos(events,photos,metadata,settings.mrk,settings.time_offset)
        # Los motores ANSI trabajan con nombres simples en un directorio ASCII.
        staging_root=cache_dir('ppk_motor'); staging_root.mkdir(exist_ok=True)
        if not str(staging_root).isascii():
            raise PPKError('Instala PPK_Dron en una ruta sin acentos para ejecutar RTKLIB (por ejemplo C:\\PPK_Dron).')
        warnings=[]
        if baseline>20000: warnings.append(f'Base distante: {baseline/1000:.2f} km; los errores atmosféricos pueden limitar la solución.')
        if rover['end']-rover['start']<600: warnings.append('Registro rover menor de 10 minutos: inicialización de ambigüedades limitada.')
        if any(e.north_mm==e.east_mm==e.down_mm==0 for e in events): warnings.append('El MRK contiene offsets antena/cámara en cero. El modelo nominal P4 es aproximado; estas fotos nunca se marcan FIX_CONCORDANTE.')
        warnings.append('Alturas elipsoidales: no son cotas ortométricas ni alturas sobre el terreno; no se aplicó geoide.')
        warnings.append('Sin puntos de comprobación independientes. Covarianzas y diferencias adelante/atrás no son garantía de exactitud absoluta.')
        warnings.append('Sin calibración ANTEX de la antena del dron; se conserva su referencia de fase efectiva. El modelo nominal sólo aproxima la distancia antena/cámara.')
        warnings.append('Tabla GPS-UTC incorporada hasta 2017 (+18 s para este vuelo); revisar al procesar campañas posteriores a un nuevo segundo intercalar.')
        with tempfile.TemporaryDirectory(prefix='run_',dir=staging_root) as td:
            stage=Path(td)
            shutil.copy2(settings.rover,stage/'rover.obs'); shutil.copy2(settings.base,stage/'base.obs')
            # Se conserva el archivo de calibración completo junto a los resultados para reproducibilidad.
            shutil.copy2(atx,stage/'antenas.atx')
            navs=[]
            for i,p in enumerate(settings.nav):
                dest=stage/f'nav_{i}.rnx'
                if normalize_nav(p,dest):
                    msg=f'{Path(p).name}: encabezado Galileo corregido de 2.11/E a 3.03/N en copia; cuerpo ya RINEX 3.'
                    warnings.append(msg); log(msg)
                navs.append(dest.name)
            solutions={}
            try:
                for direction in ('combined','forward','backward'):
                    if cancel.is_set(): raise Cancelled('Cálculo cancelado.')
                    log('Calculando '+{'combined':'solución combinada','forward':'control hacia adelante','backward':'control hacia atrás'}[direction]+'...')
                    iono=write_config(stage/(direction+'.conf'),settings,base_llh,height,antenna,'antenas.atx',baseline,direction,base)
                    args=[rtk,'-k',direction+'.conf','-o',direction+'.pos','-x','2','rover.obs','base.obs',*navs]
                    run_cmd(args,cwd=stage,logfile=stage/(direction+'.log'),cancel=cancel)
                    solutions[direction]=parse_pos(stage/(direction+'.pos'))
            finally:
                for p in stage.iterdir():
                    if p.is_file(): shutil.copy2(p,work/p.name)
        trace='\n'.join(p.read_text(errors='replace') for p in work.glob('*.trace'))
        if 'unsupported rinex type' in trace:
            raise PPKError('RTKLIB no pudo leer uno de los RINEX; consulta Calculo/*.trace.')
        if antenna and f'no receiver antenna pcv: {antenna}' in trace:
            warnings.append(f'No se encontró calibración de antena {antenna}; la altura puede contener un sesgo.')
        times={k:[x.t for x in v] for k,v in solutions.items()}
        rows=[]
        for ev,photo,method in pairs:
            if cancel.is_set(): raise Cancelled('Análisis cancelado.')
            row={'foto':photo.name if photo else '', 'disparo':ev.index,'semana_gps':ev.week,'segundos_gps':ev.sow,
                 'utc':gps_utc(ev.t).isoformat(timespec='microseconds')+'Z','asociacion':method,
                 'estado':'SIN_SOLUCION','exportar':False,'motivo':'','exportada':''}
            if photo:
                row['original']=str(photo); row['sha256_original']=sha256(photo)
                try:
                    if not base['start']<=ev.t<=base['end']: raise PPKError('Disparo sin cobertura de la base.')
                    bi=bisect_left(base['epochs'],ev.t)
                    if 0<bi<len(base['epochs']) and base['epochs'][bi]-base['epochs'][bi-1]>30.01:
                        raise PPKError('Hueco mayor de 30 s en la base.')
                    s=interpolate(solutions['combined'],times['combined'],ev.t,settings.max_gap)
                    f=interpolate(solutions['forward'],times['forward'],ev.t,settings.max_gap)
                    b=interpolate(solutions['backward'],times['backward'],ev.t,settings.max_gap)
                    dh,dv=separation(f,b)
                    lat,lon,h,comp,floor=camera_position(s,ev,metadata[photo.name],settings.lever)
                    reasons=[]
                    if any(x.q!=1 for x in (s,f,b)): reasons.append('Ambigüedades no fijadas en las tres soluciones')
                    if dh>settings.max_fb_h or dv>settings.max_fb_v: reasons.append('Discrepancia adelante/atrás')
                    if floor>0: reasons.append('Compensación antena/cámara aproximada o pendiente')
                    if settings.base_mode=='header': reasons.append('Coordenadas de base sólo aproximadas del encabezado')
                    if any('No se encontró calibración' in w for w in warnings): reasons.append('Falta calibración de antena base')
                    if max(abs(x.age) for x in (s,f,b))>30: reasons.append('Edad de base superior a 30 s')
                    if max(x.sn for x in (s,f,b))>0.10 or max(x.se for x in (s,f,b))>0.10 or max(x.su for x in (s,f,b))>0.20:
                        reasons.append('Desviación estándar interna elevada')
                    status='PROVISIONAL_'+Q_LABEL[s.q] if reasons else 'FIX_CONCORDANTE'
                    row.update(latitud=lat,longitud=lon,altura_elipsoidal_m=h,calidad_rtklib=Q_LABEL[s.q],
                               calidad_adelante=Q_LABEL[f.q],calidad_atras=Q_LABEL[b.q],satelites=s.ns,ratio=s.ratio,
                               sigma_n_rtklib_m=s.sn,sigma_e_rtklib_m=s.se,sigma_h_rtklib_m=s.su,
                               diferencia_fb_horizontal_m=dh,diferencia_fb_vertical_m=dv,compensacion=comp,
                               incertidumbre_n_m=max(s.sn,f.sn,b.sn,dh/2,floor),
                               incertidumbre_e_m=max(s.se,f.se,b.se,dh/2,floor),
                               incertidumbre_h_m=max(s.su,f.su,b.su,dv/2,floor),
                               estado=status,motivo='; '.join(reasons),exportar=(not reasons or settings.export_provisional))
                    old_h=metadata[photo.name].get('Composite:GPSAltitude')
                    if old_h is not None: row['cambio_altura_m']=h-float(old_h)
                except PPKError as exc: row['motivo']=str(exc)
            else: row['motivo']=method
            rows.append(row)
        for photo in unused:
            rows.append({'foto':photo.name,'original':str(photo),'estado':'SIN_DISPARO_MRK',
                         'motivo':'No corresponde a un disparo MRK de este vuelo; se conserva el original.', 'exportar':False})
        log(f'Asociación: {sum(p is not None for _,p,_ in pairs)} fotos; {len(unused)} sin disparo MRK.')
        export_photos(rows,exif,result,work,cancel,log)
        fields=list(dict.fromkeys(k for row in rows for k in row))
        with (result/'coordenadas_y_calidad.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        with (result/'para_Metashape.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f); writer.writerow(['foto','longitud','latitud','altura_elipsoidal_m','precision_x_m','precision_y_m','precision_z_m','estado'])
            for row in rows:
                if row.get('exportada'): writer.writerow([row['foto'],row['longitud'],row['latitud'],row['altura_elipsoidal_m'],row['incertidumbre_e_m'],row['incertidumbre_n_m'],row['incertidumbre_h_m'],row['estado']])
        counts=dict(collections.Counter(row['estado'] for row in rows))
        summary={'estado':'COMPLETADO','resultado':str(result),'conteos':counts,
                 'fotos_exportadas':sum(bool(r.get('exportada')) for r in rows),
                 'disparos':len(events),'fotos':len(photos),'base':base['marker'],'base_llh_placa':base_llh,
                 'altura_antena_m':height,'antena':antenna,'marco':frame,'modelo_ionosfera':iono,
                 'baseline_km':baseline/1000,'intervalo_base_s':base['interval'],
                 'intervalo_rover_s':rover['interval'],'duracion_rover_s':rover['end']-rover['start'],
                 'advertencias':warnings,'settings':asdict(settings),
                 'sha256_insumos':{str(p):sha256(p) for p in [settings.rover,settings.base,settings.mrk,*settings.nav]},
                 'motor_sha256':sha256(rtk),'exiftool_sha256':sha256(exif),'antenas_sha256':sha256(atx)}
        for key in ('diferencia_fb_horizontal_m','diferencia_fb_vertical_m','cambio_altura_m'):
            v=sorted(r[key] for r in rows if key in r)
            if v: summary[key]={'min':v[0],'mediana':v[len(v)//2],'max':v[-1]}
        for key,label in [('diferencia_fb_horizontal_m','Diferencia horizontal adelante/atrás'),
                          ('diferencia_fb_vertical_m','Diferencia vertical adelante/atrás'),
                          ('cambio_altura_m','Cambio de altura respecto al EXIF original')]:
            if key in summary:
                stat=summary[key]
                warnings.append(f'{label}: mínimo {stat["min"]:.3f} m, mediana {stat["mediana"]:.3f} m, máximo {stat["max"]:.3f} m.')
        (result/'resumen.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
        report=['PPK DRON — INFORME DE PROCESAMIENTO','Estado: COMPLETADO',
                f'Fotos: {len(photos)} | Disparos: {len(events)} | Exportadas: {summary["fotos_exportadas"]}',
                f'Resultados: {counts}',f'Base: {base["marker"]}; distancia aproximada: {baseline/1000:.2f} km',
                f'Base cada {base["interval"]:g} s; rover cada {rover["interval"]:g} s.',
                f'Vuelo GPST: {gps_datetime(rover["start"])} a {gps_datetime(rover["end"])}',
                f'Base placa: lat={base_llh[0]:.10f}, lon={base_llh[1]:.10f}, h={base_llh[2]:.4f} m; altura antena={height:.4f} m.',
                frame,'','CALIDAD Y USO',
                'FIX_CONCORDANTE exige FIX en ambos sentidos y combinación, concordancia y offsets MRK válidos.',
                'PROVISIONAL_* no acredita exactitud centimétrica: usar para revisión, no como control topográfico definitivo.',
                'Las precisiones CSV son indicadores conservadores internos: máximo de sigmas, media discrepancia F/B y margen de 0.20 m si la compensación es nominal. No son una certificación estadística.',
                'El modelo P4 nominal usa FRD (36, 0, 192) mm rotado con actitud de vuelo EXIF; no calibra la antena del dron.',
                'Las coordenadas EXIF se expresan en grados y altura elipsoidal. Se borró GPSMapDatum si existía para evitar atribuir un datum WGS84 no transformado.',
                'RtkFlag=0 en copias provisionales; no se presentan como RTK FIX. RelativeAltitude y tiempos de captura originales se conservan.',
                'Las fotos sin MRK no se extrapolan ni se exportan como corregidas.',
                'Las fotos originales permanecen intactas. Se verificó el flujo JPEG y las coordenadas EXIF/XMP de cada copia.',
                '',*warnings,'','ARCHIVOS',
                'coordenadas_y_calidad.csv: asociación, posiciones, motivos y controles por foto.',
                'para_Metashape.csv: importar como Longitud, Latitud, Altura; revisar datum/época y precisión antes de ajustar.',
                'Calculo/: RINEX usados, configuraciones relativas reproducibles, soluciones y registros.',
                'resumen.json: parámetros, conteos y huellas SHA256 para auditoría.',
                '', 'FUENTES',ICHI_SOURCE,'https://ag.dji.com/phantom-4-rtk/faq',
                'https://dl.djicdn.com/downloads/phantom_4_rtk/20181015/Phantom_4_RTK_User_Manual_v1.4_EN.pdf',
                'https://github.com/rtklibexplorer/RTKLIB/releases/tag/v2.5.1','https://exiftool.org/']
        (result/'INFORME.txt').write_text('\n'.join(report),encoding='utf-8')
        (result/'PROCESO_EN_CURSO.txt').unlink()
        log(f'Completado: {summary["fotos_exportadas"]} fotos exportadas. {counts}')
        return summary
    except Exception as exc:
        (result/'PROCESO_INCOMPLETO.txt').write_text(f'No usar fotos parciales como resultado final.\n{type(exc).__name__}: {exc}\n',encoding='utf-8')
        raise


def autodetect(folder):
    p=Path(folder)
    settings=Settings(photos=str(p),output=str(p.parent/'RESULTADOS_PPK'))
    mrks=sorted(x for x in p.iterdir() if x.suffix.lower()=='.mrk')
    obs=[x for x in p.iterdir() if x.suffix.lower()=='.obs']
    if len(mrks)==1: settings.mrk=str(mrks[0])
    if len(obs)==1: settings.rover=str(obs[0])
    base_dir=p/'Archivos GNSS'
    if base_dir.is_dir():
        files=sorted(x for x in base_dir.iterdir() if x.is_file())
        bases=[x for x in files if re.search(r'\.(obs|\d{2}o)$',x.name,re.I)]
        nav=[x for x in files if re.search(r'\.(nav|\d{2}[nglph])$',x.name,re.I)]
        if len(bases)==1:
            settings.base=str(bases[0])
            if rinex_info(bases[0])['marker'].upper()=='ICHI': settings.base_mode='ichi'
        settings.nav=[str(x) for x in nav]
    return settings
