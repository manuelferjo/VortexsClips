"""
Actualizador de Vortexs Clips.
Mira la última versión publicada en GitHub Releases y, si es más nueva,
descarga el zip y sustituye los archivos del programa.
Tu configuración (clave, sincronizaciones…) está en %APPDATA%\\VortexsClips y no se toca.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from version import REPO, VERSION

FROZEN = getattr(sys, "frozen", False)          # True en la versión instalada (.exe)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
API = f"https://api.github.com/repos/{REPO}/releases/latest"
KEEP = {"ffmpeg"}   # carpetas locales que nunca se borran ni se pisan


def _ver(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    out = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def latest() -> dict:
    """Última versión publicada: {'version','url' (zip),'setup' (instalador .exe o None),'notes'}."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "VortexsClips-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rel = json.loads(r.read().decode("utf-8"))
    assets = rel.get("assets", [])
    z = next((a for a in assets if a.get("name", "").lower().endswith(".zip")), None)
    st = next((a for a in assets if a.get("name", "").lower().endswith("setup.exe")), None)
    return {"version": rel.get("tag_name", "").lstrip("vV"),
            "url": z["browser_download_url"] if z else rel.get("zipball_url"),
            "setup": st["browser_download_url"] if st else None,
            "notes": rel.get("body") or ""}


def check() -> dict | None:
    """La última versión si es más nueva que la instalada; si no, None."""
    info = latest()
    if not info["version"] or _ver(info["version"]) <= _ver(VERSION):
        return None
    return info


def run_installer(url: str, progress=None, silent: bool = True) -> None:
    """Descarga el instalador y lo lanza. En silencio: actualiza y vuelve a abrir el programa solo."""
    dst = os.path.join(tempfile.gettempdir(), "VortexsClips-Setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": "VortexsClips-Updater"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress and total:
                progress(done / total)
    os.replace(dst + ".part", dst)
    args = [dst]
    if silent:
        args += ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(args, creationflags=flags, close_fds=True)
    os._exit(0)


def apply(url: str, progress=None) -> None:
    """Descarga el zip de la versión nueva y copia sus archivos encima del programa."""
    tmp = tempfile.mkdtemp(prefix="vortexsclips_upd_")
    try:
        zpath = os.path.join(tmp, "update.zip")
        req = urllib.request.Request(url, headers={"User-Agent": "VortexsClips-Updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        ex = os.path.join(tmp, "x")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ex)
        # localizar la carpeta que contiene app.py dentro del zip
        src = None
        for root, _dirs, files in os.walk(ex):
            if "app.py" in files and "core.py" in files:
                src = root
                break
        if not src:
            raise RuntimeError("El paquete descargado no parece una versión de Vortexs Clips.")
        for name in os.listdir(src):
            if name in KEEP or name.startswith("."):
                continue
            s = os.path.join(src, name)
            d = os.path.join(APP_DIR, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        shutil.rmtree(os.path.join(APP_DIR, "__pycache__"), ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart() -> None:
    if FROZEN:
        subprocess.Popen([sys.executable], cwd=APP_DIR)
        os._exit(0)
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.isfile(pyw):
            exe = pyw
    subprocess.Popen([exe, os.path.join(APP_DIR, "app.py")], cwd=APP_DIR)
    os._exit(0)
