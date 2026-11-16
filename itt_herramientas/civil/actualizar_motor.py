"""Copia fijada del motor Civil (`tvcivil`, repositorio privado cadaritre/TresVizo-Civil) dentro de Herramientas.

El servidor nunca descarga nada ni tiene acceso al repositorio: el motor viaja en el código de Herramientas, en `civil/motor/`, copiado
de un commit exacto. `MOTOR.json` guarda el repositorio, el commit y el SHA-256 de cada archivo; `pruebas/test_civil.py` comprueba que la
copia no se tocó a mano. Para actualizar (en la Mac, con el repositorio del motor al lado):

    python3 itt_herramientas/civil/actualizar_motor.py --repo ~/Documents/TresVizo-Civil --ref <commit o tag>

y correr las pruebas de Civil (unitarias y de navegador).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
MOTOR = AQUI / "motor"


def huellas(carpeta: Path) -> dict[str, str]:
    return {str(p.relative_to(carpeta)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(carpeta.rglob("*.py")) if "__pycache__" not in p.parts}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--ref", required=True)
    a = ap.parse_args(argv)
    commit = subprocess.run(["git", "-C", str(a.repo), "rev-parse", "--verify", a.ref + "^{commit}"], check=True, capture_output=True, text=True).stdout.strip()
    sucio = subprocess.run(["git", "-C", str(a.repo), "status", "--porcelain", "--", "tvcivil"], capture_output=True, text=True).stdout.strip()
    if sucio and a.ref == "HEAD":
        raise SystemExit("FALLA: el motor tiene cambios sin confirmar; confirma antes de copiarlo.")
    datos = subprocess.run(["git", "-C", str(a.repo), "archive", "--format=tar", commit, "tvcivil", "pyproject.toml"], check=True, capture_output=True).stdout
    nuevo = AQUI / "motor.nuevo"
    shutil.rmtree(nuevo, ignore_errors=True)
    nuevo.mkdir()
    version = ""
    with tarfile.open(fileobj=io.BytesIO(datos)) as t:
        for m in t.getmembers():
            if m.name == "pyproject.toml":
                texto = t.extractfile(m).read().decode()
                version = next(l.split("=", 1)[1].strip().strip('"') for l in texto.splitlines() if l.startswith("version"))
                continue
            if not m.isfile() or not m.name.startswith("tvcivil/") or ".." in m.name:
                continue
            destino = nuevo / m.name[len("tvcivil/"):]
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(t.extractfile(m).read())
    (nuevo / "MOTOR.json").write_text(json.dumps({
        "_nota": "Copia fijada de tvcivil. No se edita aquí: se cambia en el repositorio del motor y se vuelve a copiar con actualizar_motor.py.",
        "repo": "cadaritre/TresVizo-Civil", "commit": commit, "version": version, "archivos": huellas(nuevo),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    shutil.rmtree(MOTOR, ignore_errors=True)
    nuevo.rename(MOTOR)
    print(f"OK    motor tvcivil {version} ({commit[:9]}) copiado en {MOTOR}")


if __name__ == "__main__":
    main()
