"""
Avisos por Telegram: cuando termina un directo (vídeos listos, subido a Drive o algún fallo), un mensaje
a tu móvil con el TOP 3 y el mejor clip en vertical.

Se usa un bot tuyo (lo creas en Telegram con @BotFather, que te da su «token»). Para saber a qué chat
mandarlo, le escribes cualquier cosa al bot una vez y el programa lo lee.
"""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse
import urllib.request
import uuid

API = "https://api.telegram.org/bot{token}/{method}"
MAX_VIDEO = 49 * 1024 * 1024        # los bots pueden mandar archivos de hasta 50 MB


def _call(token: str, method: str, params: dict | None = None, timeout: int = 30) -> dict:
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(API.format(token=token.strip(), method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            res = json.loads(e.read().decode("utf-8"))
        except Exception:
            raise RuntimeError(f"Telegram: error {e.code}") from None
    if not res.get("ok"):
        raise RuntimeError("Telegram: " + str(res.get("description") or res)[:200])
    return res.get("result")


def bot_name(token: str) -> str:
    me = _call(token, "getMe")
    return "@" + (me.get("username") or "?")


def find_chat(token: str) -> tuple[str, str] | None:
    """El chat de la última persona que le escribió al bot -> (chat_id, nombre), o None."""
    ups = _call(token, "getUpdates", {"limit": 50, "timeout": 0}) or []
    for u in reversed(ups):
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        ch = msg.get("chat") or {}
        if ch.get("id") is not None:
            who = ch.get("first_name") or ch.get("title") or ch.get("username") or ""
            return str(ch["id"]), who
    return None


def send(token: str, chat_id: str, text: str) -> None:
    _call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"})


def send_video(token: str, chat_id: str, path: str, caption: str = "") -> bool:
    """Manda un vídeo (si pesa menos de 50 MB). True si se mandó."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_VIDEO:
        return False
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat_id), ("caption", caption[:1000]), ("supports_streaming", "true")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    fname = os.path.basename(path).encode("ascii", "replace").decode("ascii").replace('"', "'")
    ctype = mimetypes.guess_type(path)[0] or "video/mp4"
    with open(path, "rb") as f:
        body_file = f.read()
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n".encode("utf-8") + body_file + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(API.format(token=token.strip(), method="sendVideo"), data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("ok"):
        raise RuntimeError("Telegram: " + str(res.get("description"))[:200])
    return True
