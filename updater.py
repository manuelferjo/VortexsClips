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
    by = {a.get("name", "").lower(): a.get("browser_download_url") for a in assets}
    z = by.get("vortexsclips.zip")
    return {"version": rel.get("tag_name", "").lstrip("vV"),
            "url": z or rel.get("zipball_url"),
            "setup": by.get("vortexsclips-setup.exe"),
            "app": by.get("vortexsclips-app.zip"),          # solo el programa (actualización rápida)
            "deps": by.get("vortexsclips-deps.txt"),        # huella de Python, numpy… de esa versión
            "notes": rel.get("body") or ""}


def check() -> dict | None:
    """La última versión si es más nueva que la instalada; si no, None."""
    info = latest()
    if not info["version"] or _ver(info["version"]) <= _ver(VERSION):
        return None
    return info


def _download(url: str, dst: str, progress=None) -> None:
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


def swap_exe(exe: str, new_exe: str) -> None:
    """Pone new_exe en lugar de exe aunque exe esté abierto: Windows deja renombrar un programa en
    marcha (no borrarlo), así que el viejo pasa a «*.old.exe» y se borra en el siguiente arranque."""
    old = exe[:-4] + ".old.exe"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        old = exe[:-4] + f".old{os.getpid()}.exe"
    os.replace(exe, old)
    try:
        shutil.copy2(new_exe, exe)
    except Exception:
        os.replace(old, exe)           # deshacer: el programa sigue como estaba
        raise


def cleanup_old() -> None:
    """Borra el programa viejo que dejó una actualización rápida."""
    if not FROZEN:
        return
    try:
        for n in os.listdir(APP_DIR):
            if n.lower().startswith("vortexsclips.old") and n.lower().endswith(".exe"):
                try:
                    os.remove(os.path.join(APP_DIR, n))
                except OSError:
                    pass
    except OSError:
        pass


def fast_update(info: dict, progress=None) -> bool:
    """
    Actualización rápida: si Python, numpy y lo demás no han cambiado (misma «huella»), solo se baja
    el programa (unos MB, no el instalador de ~80 MB), se cambia y se vuelve a abrir. Si no se puede,
    devuelve False y se usa el instalador completo.
    """
    if not FROZEN or not info.get("app") or not info.get("deps"):
        return False
    try:
        with open(os.path.join(APP_DIR, "deps.txt"), encoding="utf-8") as f:
            local = f.read().strip()
        req = urllib.request.Request(info["deps"], headers={"User-Agent": "VortexsClips-Updater"})
        with urllib.request.urlopen(req, timeout=30) as r:
            remote = r.read().decode("utf-8", "ignore").strip()
        if not local or local != remote:
            return False
        tmp = tempfile.mkdtemp(prefix="vortexsclips_fast_")
        z = os.path.join(tmp, "app.zip")
        _download(info["app"], z, progress)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        new_exe = next((os.path.join(root, n) for root, _d, fs in os.walk(tmp) for n in fs
                        if n.lower() == "vortexsclips.exe"), None)
        if not new_exe or os.path.getsize(new_exe) < 1_000_000:
            return False
        exe = sys.executable
        swap_exe(exe, new_exe)
        try:
            shutil.copy2(os.path.join(os.path.dirname(new_exe), "deps.txt"), os.path.join(APP_DIR, "deps.txt"))
        except OSError:
            pass
    except Exception:
        return False
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    subprocess.Popen([sys.executable], creationflags=flags, close_fds=True, cwd=APP_DIR)
    os._exit(0)


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
