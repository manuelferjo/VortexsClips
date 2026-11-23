"""
Reproductor integrado de Vortexs Clips.
Imagen: ffmpeg decodifica a 640x360 y se pinta en un Canvas de tkinter.
Sonido: ffplay sin ventana, arrancado a la vez (se reinicia al pausar/saltar).
Siempre muestra el minuto exacto del VOD.
"""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

import core

VW, VH, FPS = 640, 360, 24
BG = "#0d0b10"
YELLOW = "#ffd21f"
MUTED = "#9a91a8"


class Player(ttk.Frame):
    def __init__(self, master, get_vod, on_pick=None, style="Panel.TFrame"):
        super().__init__(master, style=style)
        self.get_vod = get_vod
        self.on_pick = on_pick
        self.range = (0.0, 60.0)
        self.pos = 0.0
        self.playing = False
        self.procs = []
        self.q = queue.Queue(maxsize=FPS * 2)
        self.gen = 0
        self._img = None
        self._raw_ok = True
        self._tick_id = None

        self.cv = tk.Canvas(self, width=VW, height=VH, bg="black", highlightthickness=0)
        self.cv.pack(fill="both", expand=False)
        self.img_id = self.cv.create_image(0, 0, anchor="nw")
        self.clock_bg = self.cv.create_rectangle(VW / 2 - 95, 8, VW / 2 + 95, 44, fill="#000000",
                                                 outline="", stipple="gray50")
        self.clock = self.cv.create_text(VW / 2, 26, text="--:--:--", fill=YELLOW,
                                         font=("Segoe UI Black", 18))
        self.msg = self.cv.create_text(VW / 2, VH / 2, text="Elige una ronda para verla aquí",
                                       fill=MUTED, font=("Segoe UI", 12))

        self.v_scale = tk.DoubleVar(value=0)
        self.scale = ttk.Scale(self, from_=0, to=60, variable=self.v_scale, orient="horizontal")
        self.scale.pack(fill="x", pady=(6, 2))
        self.scale.bind("<ButtonRelease-1>", lambda e: self.seek(self.v_scale.get()))

        bar = ttk.Frame(self, style=style)
        bar.pack(fill="x")
        for txt, d in (("−1 min", -60), ("−10 s", -10)):
            ttk.Button(bar, text=txt, width=7, command=lambda d=d: self.seek(self.pos + d)).pack(side="left", padx=(0, 4))
        self.btn_play = ttk.Button(bar, text="▶  Reproducir", width=14, style="Purple.TButton", command=self.toggle)
        self.btn_play.pack(side="left", padx=4)
        for txt, d in (("+10 s", 10), ("+1 min", 60)):
            ttk.Button(bar, text=txt, width=7, command=lambda d=d: self.seek(self.pos + d)).pack(side="left", padx=(0, 4))
        if on_pick:
            ttk.Button(bar, text="✔ Usar este momento", style="Accent.TButton",
                       command=lambda: on_pick(self.pos)).pack(side="right")
        self.lbl_range = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_range.pack(side="right", padx=8)

        for w in (self, self.cv):
            w.bind("<space>", lambda e: self.toggle())
            w.bind("<Left>", lambda e: self.seek(self.pos - 10))
            w.bind("<Right>", lambda e: self.seek(self.pos + 10))
            w.bind("<Up>", lambda e: self.seek(self.pos + 60))
            w.bind("<Down>", lambda e: self.seek(self.pos - 60))
        self.cv.bind("<Button-1>", lambda e: (self.cv.focus_set(), self.toggle()))
        self.bind("<Destroy>", lambda e: self._kill() if e.widget is self else None)

    # ------------------------------------------------------------------ API
    def open(self, start, end=None, autoplay=True):
        start = max(0.0, float(start))
        end = float(end) if end else start + 300
        self.range = (max(0.0, start - 30), end + 30)
        self.scale.configure(from_=self.range[0], to=self.range[1])
        self.lbl_range.configure(text=f"{core.fmt_ts(start).split('.')[0]} → {core.fmt_ts(end).split('.')[0]}")
        self.cv.itemconfigure(self.msg, text="")
        self.pos = start
        self.cv.focus_set()
        if autoplay:
            self.play()
        else:
            self.seek(start)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self):
        if not self.get_vod():
            return
        self.playing = True
        self.btn_play.configure(text="⏸  Pausa")
        self._start(self.pos, audio=True)

    def pause(self):
        if self.playing:
            self.pos = self._now()
        self.playing = False
        self.btn_play.configure(text="▶  Reproducir")
        self._kill()

    def seek(self, t):
        t = max(0.0, min(float(t), self.range[1]))
        self.pos = t
        if self.playing:
            self._start(t, audio=True)
        else:
            self._start(t, audio=False, single=True)
        self._show_clock(t)

    def stop(self):
        self.pause()

    # ------------------------------------------------------------------ interno
    def _now(self):
        return self.pos0 + (time.time() - self.t0) if self.playing else self.pos

    def _kill(self):
        self.gen += 1
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        self.procs = []
        with self.q.mutex:
            self.q.queue.clear()
        if self._tick_id:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _start(self, t, audio, single=False):
        self._kill()
        vod = self.get_vod()
        ffmpeg = core.find_tool("ffmpeg")
        if not vod or not ffmpeg:
            return
        gen = self.gen
        vf = (f"fps={FPS},scale={VW}:{VH}:force_original_aspect_ratio=decrease,"
              f"pad={VW}:{VH}:(ow-iw)/2:(oh-ih)/2")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", vod,
               "-an", "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo"]
        if single:
            cmd += ["-frames:v", "1"]
        cmd.append("pipe:1")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **core._no_window())
        self.procs.append(p)
        threading.Thread(target=self._reader, args=(p, gen), daemon=True).start()
        if audio:
            ffplay = core.find_tool("ffplay")
            if ffplay:
                a = subprocess.Popen([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{t:.3f}",
                                      vod], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **core._no_window())
                self.procs.append(a)
        self.pos0 = t
        self.t0 = time.time()
        self.frame_i = -1
        self._tick_id = self.after(10, self._tick, gen, single)

    def _reader(self, p, gen):
        size = VW * VH * 3
        i = 0
        while gen == self.gen:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            while gen == self.gen:
                try:
                    self.q.put((i, b), timeout=0.2)
                    break
                except queue.Full:
                    continue
            i += 1
        try:
            self.q.put((None, None), timeout=0.5)
        except queue.Full:
            pass

    def _tick(self, gen, single):
        if gen != self.gen:
            return
        target = int((time.time() - self.t0) * FPS) if not single else 0
        frame = None
        ended = False
        while True:
            try:
                i, b = self.q.get_nowait()
            except queue.Empty:
                break
            if i is None:
                ended = True
                break
            frame = (i, b)
            if i >= target:
                break
        if frame:
            self._paint(frame[1])
            self.frame_i = frame[0]
            if single:
                return
        cur = self.pos0 + max(self.frame_i, 0) / FPS
        if self.playing:
            self.pos = cur
            self._show_clock(cur)
            if cur >= self.range[1] or ended:
                self.pause()
                return
        self._tick_id = self.after(12, self._tick, gen, single)

    def _paint(self, rgb: bytes):
        header = f"P6 {VW} {VH} 255 ".encode()
        try:
            if self._raw_ok:
                img = tk.PhotoImage(data=header + rgb, format="PPM")
            else:
                raise tk.TclError
        except tk.TclError:
            # alternativa: pasar por un archivo temporal
            self._raw_ok = False
            path = os.path.join(core.data_dir(), "_preview.ppm")
            with open(path, "wb") as f:
                f.write(header + rgb)
            img = tk.PhotoImage(file=path)
        self._img = img
        self.cv.itemconfigure(self.img_id, image=img)
        self.cv.tag_raise(self.clock_bg)
        self.cv.tag_raise(self.clock)

    def _show_clock(self, t):
        self.cv.itemconfigure(self.clock, text=core.fmt_ts(t).split(".")[0] if t < 3600 else
                              core.fmt_ts(t)[:8])
        try:
            self.v_scale.set(t)
        except tk.TclError:
            pass
