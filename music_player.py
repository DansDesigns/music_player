#!/usr/bin/env python3
"""
WavePlayer — v1.0.0
A standalone voice-controlled media player with Vosk STT, TTS, and
an animated waveform circle.

Features:
  • Audio (mp3/ogg/wav/flac/m4a/opus) and Video (mp4/avi/mkv/webm) via pygame + cv2
  • Album art / box-art rendered inside the circle
  • Animated equaliser bars
  • Full transport controls arc-rendered below the circle
  • Vosk STT — speech detection ducks media volume and shows popup
  • TTS via espeak-ng (Linux) / SAPI5 (Windows) / pyttsx3 fallback
  • Wallpaper browser
  • Settings panel (transparency sliders, mic threshold, font picker)
  • IPC (Unix socket / TCP) for external control

Voice commands:
  play / pause / stop / next / previous / shuffle / repeat
  volume up / volume down / set volume 70
  play <song name>  /  play <artist name>
  play anything by <artist>
  open music folder / open media folder
  set wallpaper / browse wallpapers
  show/open settings / close settings
  show help / close help
  stop listening / mute mic / start listening / unmute mic
  ESC — quit
"""

import pygame
import pygame.gfxdraw
import os, sys, math, time, random, argparse, json, platform
import threading, socket, subprocess
import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog
    TK_OK = True
except ImportError:
    TK_OK = False

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from pathlib import Path

# ── Optional imports ───────────────────────────────────────────────────────────
try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False

try:
    from vosk import Model, KaldiRecognizer
    import sounddevice as sd
    VOSK_OK = True
except ImportError:
    VOSK_OK = False
    print("WARNING: vosk/sounddevice not installed — STT disabled.")

try:
    import pyttsx3
    PYTTSX3_OK = True
except ImportError:
    PYTTSX3_OK = False

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from mutagen import File as MutagenFile
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"[DEBUG] Script dir: {_SCRIPT_DIR}")
print(f"[DEBUG] CWD: {os.getcwd()}")
_model_path = os.path.join(_SCRIPT_DIR, "models", "EN")
print(f"[DEBUG] Looking for model at: {_model_path}")
print(f"[DEBUG] Exists: {os.path.isdir(_model_path)}")
if os.path.isdir(_model_path):
    print(f"[DEBUG] Contents: {os.listdir(_model_path)}")

# ── OS / IPC config ────────────────────────────────────────────────────────────

def _detect_ipc_mode():
    _IS_WINDOWS = platform.system() == "Windows"
    _unix_available = hasattr(socket, "AF_UNIX") and not _IS_WINDOWS
    if _unix_available:
        return {"mode": "unix", "socket_path": "/tmp/waveplayer.sock", "host": None, "port": None}
    else:
        return {"mode": "tcp", "socket_path": None, "host": "127.0.0.1", "port": 47900}

IPC_CFG = _detect_ipc_mode()

VERSION  = "1.0.1"
APP_NAME = "WavePlayer"
REPO_URL = "https://github.com/dansdesigns/music_player.git"  # update to your repo URL

# ── Persistent settings ────────────────────────────────────────────────────────

SETTINGS_FILE  = os.path.expanduser("~/.waveplayer_settings.json")
WALLPAPER_FILE = os.path.expanduser("~/.waveplayer_wallpaper")
PLAYBACK_FILE  = os.path.expanduser("~/.waveplayer_playback.json")

_SETTINGS_DEFAULTS = {
    "panel_alpha":   0.82,
    "bar_alpha":     0.12,
    "mic_threshold": 0.10,
    "ui_font":       "",
    "tts_rate":      175,
    "tts_volume":    1.0,
    "wake_word":     "",
    "show_date":     1.0,
    "mic_muted":     0.0,
}

# Keys whose values should be kept as strings rather than cast to float
_SETTINGS_STR_KEYS = {"ui_font", "wake_word"}

def load_settings() -> dict:
    s = dict(_SETTINGS_DEFAULTS)
    if os.path.exists(SETTINGS_FILE):
        try:
            stored = json.load(open(SETTINGS_FILE))
            for k in _SETTINGS_DEFAULTS:
                if k in stored:
                    s[k] = str(stored[k]) if k in _SETTINGS_STR_KEYS else float(stored[k])
        except Exception as e:
            print(f"[Settings] Load error: {e}")
    return s

def save_settings(s: dict):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
        os.chmod(SETTINGS_FILE, 0o600)
    except Exception as e:
        print(f"[Settings] Save error: {e}")

def save_playback(track_path: str, position: float, volume: float,
                  shuffle: bool, repeat: bool, folder: str = ""):
    """Persist current playback state so it can be restored on next launch."""
    try:
        data = {"track": track_path, "position": round(position, 2),
                "volume": round(volume, 3), "shuffle": shuffle, "repeat": repeat,
                "folder": folder}
        with open(PLAYBACK_FILE, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(PLAYBACK_FILE, 0o600)
    except Exception as e:
        print(f"[Playback] Save error: {e}")

def load_playback() -> dict:
    """Return saved playback state, or {} if none / corrupt."""
    if not os.path.exists(PLAYBACK_FILE):
        return {}
    try:
        with open(PLAYBACK_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        print(f"[Playback] Load error: {e}")
        return {}


# ── Vocabulary / command lists ─────────────────────────────────────────────────

WAKE_TIMEOUT = 8.0   # seconds to wait for command after wake word before resetting

SPOKEN_NUMBERS = {
    "zero":0,"one":1,"two":2,"three":3,"four":4,
    "five":5,"six":6,"seven":7,"eight":8,"nine":9,
}

MUTE_COMMANDS   = ["stop listening","mute microphone","mute mic","disable listening"]
UNMUTE_COMMANDS = ["start listening","unmute microphone","unmute mic","enable listening", "resume listening", "listen to me"]
WALLPAPER_COMMANDS = [
    "set wallpaper","change wallpaper","change background",
    "set background","browse wallpapers","show wallpapers"]

SETTINGS_OPEN_CMDS  = ["open settings","show settings","toggle settings","open system","show system"]
SETTINGS_CLOSE_CMDS = ["close settings","hide settings","close system","hide system"]
PLAYLIST_OPEN_CMDS  = ["show playlist","open playlist","toggle playlist"]
PLAYLIST_CLOSE_CMDS = ["hide playlist","close playlist"]

MEDIA_PLAY_CMDS    = ["play","play music","play media","resume","resume media","resume music","resume playback"]
MEDIA_PAUSE_CMDS   = ["pause playback","pause music","pause media", "pause"]
MEDIA_STOP_CMDS    = ["stop music","stop media","stop playback","stop playing", "stop"]
MEDIA_NEXT_CMDS    = ["next","next track","next song","skip","skip track"]
MEDIA_PREV_CMDS    = ["previous","previous track","previous song","last track","last song","go back"]
MEDIA_SHUFFLE_CMDS = ["shuffle","shuffle on","shuffle off","toggle shuffle","shuffle music"]
MEDIA_REPEAT_CMDS  = ["repeat","repeat on","repeat off","toggle repeat","loop","loop on","loop off"]
MEDIA_VOL_UP_CMDS  = ["volume up","louder","turn it up","increase volume","turn up"]
MEDIA_VOL_DN_CMDS  = ["volume down","quieter","turn it down","decrease volume","turn down"]
MEDIA_FOLDER_CMDS  = ["open music folder","open music","select music folder","select music",
                       "select folder","open media folder","change folder","change music folder","change music"]
MEDIA_PLAY_ARTIST_CMDS = [
    "play anything by ","play everything by ","shuffle everything by ",
    "shuffle anything by ","play all by ","play all songs by ",
]

HELP_OPEN_COMMANDS  = ["show help","open help","show commands","open commands",
                        "help","commands","list commands","what can i say"]
HELP_CLOSE_COMMANDS = ["close help","hide help","close commands","hide commands"]

ACK_PHRASES = ["working","processing","understood","accessing"]

IMG_EXT   = {".jpg",".jpeg",".png",".bmp",".gif",".webp"}
AUDIO_EXT = {".mp3",".ogg",".wav",".flac",".m4a",".opus"}
VIDEO_EXT = {".mp4",".avi",".mkv",".webm",".mov",".m4v"}

SAMPLE_RATE  = 16000
CIRCLE_ALPHA = 64

# ── Slider range constants ─────────────────────────────────────────────────────
PANEL_ALPHA_MIN = 0.0; PANEL_ALPHA_MAX = 1.0
BAR_ALPHA_MIN   = 0.0; BAR_ALPHA_MAX   = 1.0
MIC_THRESH_MIN  = 0.005; MIC_THRESH_MAX = 0.99

# ── Colours ────────────────────────────────────────────────────────────────────
BLACK       = (  0,   0,   0)
WHITE       = (255, 255, 255)
DARK_BG     = (  8,  12,  20)
ORANGE_BG   = (180,  90,  20)
ORANGE_MID  = (210, 110,  30)
ORANGE_LITE = (230, 140,  50)
BLUE_DARK   = ( 15,  35,  65)
BLUE_MID    = ( 30,  80, 130)
BLUE_LITE   = ( 80, 160, 210)
BLUE_AVATAR = ( 60, 140, 190)
GREEN_LITE  = ( 80, 200,  80)
RED_MID     = (160,  20,  20)
RED_LITE    = (220,  60,  60)
TEXT_DIM    = (120, 130, 150)
TEXT_MID_C  = (180, 190, 200)
TEXT_BRIGHT = (230, 240, 255)
TEXT_BOLD   = (255, 255, 255)
CLICK_HOVER = ( 60, 180, 200)
WAVEFORM_WHITE = (255, 255, 255)
CONV_MID    = (160,  80,  15)
CONV_LITE   = (230, 140,  40)

MEDIA_DARK   = (  8,  28,  12)
MEDIA_MID    = ( 28, 110,  45)
MEDIA_LITE   = ( 70, 210, 110)
MEDIA_ACCENT = (120, 255, 160)
MEDIA_BAR_BG = ( 10,  24,  14)

TTS_HALO_OUTER  = ( 80, 160, 210)
TTS_HALO_MID    = ( 30,  80, 130)
TTS_HALO_INNER  = ( 15,  35,  65)
TTS_HALO_FRINGE = (120, 190, 240)

HELP_ACCENT  = ( 80, 200, 200)
HELP_HDR_COL = (160, 220, 240)
HELP_KEY_COL = ( 60, 180, 180)
HELP_VAL_COL = (180, 195, 215)
HELP_CAT_COL = (100, 170, 200)
HELP_DIVIDER = ( 25,  50,  80)

CMD_LOG_TIME_COL = ( 70,  80,  95)
CMD_LOG_TEXT_COL = (120, 210, 160)

BAR_HEIGHT = 36   # default; overridden at runtime by _compute_layout()
PANEL_W    = 230  # default; overridden at runtime by _compute_layout()

def _compute_layout(W: int, H: int) -> dict:
    """Return a dict of all screen-relative layout values."""
    short = min(W, H)
    long  = max(W, H)
    portrait = H > W
    bar_h  = max(28, int(H * 0.045))          # ~4.5% of height
    # Circle: on portrait screens use width; on landscape use height
    if portrait:
        circle_r = int(W * 0.28)
        circle_cy = int(H * 0.38)
    else:
        circle_r = int(H * 0.26)
        circle_cy = H // 2
    circle_cx = W // 2
    # Panel fills from left edge (x=20) almost to the circle left edge, minus a gap
    panel_w = max(200, circle_cx - circle_r - 40)
    # Gaps — relative to circle radius
    title_gap   = int(circle_r * 0.45)   # above circle top
    time_gap    = int(circle_r * 0.38)   # below circle bottom
    ctrl_gap    = int(circle_r * 0.18)   # extra below time line
    # Button sizes relative to short edge
    btn_w = max(40, int(short * 0.055))
    btn_h = max(26, int(short * 0.034))
    vol_bar_h = max(18, int(short * 0.024))
    return dict(
        bar_h=bar_h, panel_w=panel_w,
        circle_r=circle_r, circle_cx=circle_cx, circle_cy=circle_cy,
        title_gap=title_gap, time_gap=time_gap, ctrl_gap=ctrl_gap,
        btn_w=btn_w, btn_h=btn_h, vol_bar_h=vol_bar_h,
        screen_h=H, screen_w=W,
    )


# ── Audio duration helper ─────────────────────────────────────────────────────

def _get_audio_duration(path: str) -> float:
    """Return track duration in seconds. Tries mutagen, then ffprobe, then Sound."""
    # 1. mutagen — fast, pure-python, no subprocess
    if MUTAGEN_OK:
        try:
            f = MutagenFile(path)
            if f is not None and hasattr(f, 'info') and hasattr(f.info, 'length'):
                d = float(f.info.length)
                if d > 0:
                    return d
        except Exception:
            pass
    # 2. ffprobe — works for any format
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            stderr=_sp.DEVNULL, timeout=3
        ).decode().strip()
        d = float(out)
        if d > 0:
            return d
    except Exception:
        pass
    # 3. pygame.mixer.Sound — loads full file into RAM, last resort
    try:
        snd = pygame.mixer.Sound(path)
        d = snd.get_length()
        del snd
        if d > 0:
            return d
    except Exception:
        pass
    return 0.0


# ── Drawing helpers ────────────────────────────────────────────────────────────

def draw_circle_alpha(surface, colour_rgba, centre, radius):
    if radius <= 0: return
    s = pygame.Surface((radius*2, radius*2), pygame.SRCALPHA)
    pygame.draw.circle(s, colour_rgba, (radius, radius), radius)
    surface.blit(s, (centre[0]-radius, centre[1]-radius))

def draw_rounded_rect_alpha(surface, rect, fill_rgba, border_rgba=None,
                              border_w=0, radius=10):
    w, h = rect.width, rect.height
    if w <= 0 or h <= 0: return
    tmp = pygame.Surface((w, h), pygame.SRCALPHA)
    tmp.fill((0,0,0,0))
    r = tmp.get_rect()
    kw = ({"border_top_left_radius":radius[0],"border_top_right_radius":radius[1],
            "border_bottom_left_radius":radius[2],"border_bottom_right_radius":radius[3]}
          if isinstance(radius, tuple) else {"border_radius":radius})
    pygame.draw.rect(tmp, fill_rgba, r, **kw)
    if border_rgba and border_w > 0:
        pygame.draw.rect(tmp, border_rgba, r, border_w, **kw)
    surface.blit(tmp, rect.topleft)

def lerp_col(a, b, t):
    return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(3))

def blend_col(base, tgt, speed, dt):
    return lerp_col(base, tgt, min(1.0, speed*dt))

def truncate_text(font, text: str, max_w: int) -> str:
    if font.size(text)[0] <= max_w: return text
    while text and font.size(text+"...")[0] > max_w:
        text = text[:-1]
    return text + "..."


# ── Font loader ────────────────────────────────────────────────────────────────

_FONT_REG  = ["Segoeuiemoji"]
_FONT_BOLD = ["Segoeuiemoji-Bold"]

def _ffind(paths):
    for p in paths:
        if p and os.path.isfile(p): return p
    return None

_FONT_PATH_REG  = None   # set by load_fonts for dynamic sizing
_FONT_PATH_BOLD = None

def load_fonts(font_name: str = "") -> dict:
    global _FONT_PATH_REG, _FONT_PATH_BOLD
    rp = bp = None
    if font_name:
        sp = pygame.font.match_font(font_name)
        if sp and os.path.isfile(sp):
            rp = bp = sp
    if rp is None:
        rp = _ffind(_FONT_REG); bp = _ffind(_FONT_BOLD) or rp; rp = rp or bp
    _FONT_PATH_REG = rp; _FONT_PATH_BOLD = bp
    def mk(path, size):
        return pygame.font.Font(path, size) if path else pygame.font.Font(None, size)
    return {
        "sm":mk(rp,14),"md":mk(rp,18),"lg":mk(rp,24),
        "xl":mk(rp,34),"xxl":mk(rp,50),
        "sm_b":mk(bp,13),"md_b":mk(bp,18),"lg_b":mk(bp,24),"xl_b":mk(bp,34),
        "mono_sm":mk(rp,18),"mono_md":mk(rp,19),"mono_lg":mk(bp,24),
        "mono_xl":mk(bp,34),"mono_xxl":mk(bp,50),
    }

def get_system_fonts() -> List[str]:
    return sorted(set(f for f in pygame.font.get_fonts() if f and len(f) > 1))


# ── TTS Engine ─────────────────────────────────────────────────────────────────

class TTSEngine:
    _RAMP_UP   = 0.18
    _RAMP_DOWN = 0.28
    _SUSTAIN   = 0.85
    _MIC_RELEASE_DELAY = 5.0

    def __init__(self, on_level=None, on_start=None, on_end=None):
        self.on_level  = on_level  or (lambda v: None)
        self.on_start  = on_start  or (lambda: None)
        self.on_end    = on_end    or (lambda: None)
        self.ready     = False
        self.error     = ""
        self.speaking  = False
        self.mic_muted = False
        self._queue: List[str] = []
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._rate   = 175
        self._volume = 1.0
        self._stop_flag = False
        self._level = 0.0
        self._utt_start = 0.0
        self._utt_len   = 0.0

    def start(self):
        threading.Thread(target=self._run,        daemon=True, name="TTS-engine").start()
        threading.Thread(target=self._level_loop, daemon=True, name="TTS-level").start()

    def stop(self):
        self._stop_flag = True
        self._event.set()

    def speak(self, text: str):
        text = text.strip()
        if not text: return
        with self._lock: self._queue.append(text)
        self._event.set()

    def speak_immediate(self, text: str):
        text = text.strip()
        if not text: return
        with self._lock:
            self._queue.clear()
            self._queue.append(text)
        self._event.set()

    def set_rate(self, wpm: int):   self._rate   = wpm
    def set_volume(self, vol: float): self._volume = max(0.0, min(1.0, vol))

    def _tts_begin(self):
        self.speaking  = True
        self.mic_muted = True
        self.on_start()

    def _tts_end(self):
        self.speaking  = False
        time.sleep(self._MIC_RELEASE_DELAY)
        self.mic_muted = False
        self.on_end()

    def _run(self):
        if platform.system() == "Windows": self._run_windows()
        else:                               self._run_linux()

    def _run_windows(self):
        try:
            import pythoncom, win32com.client
            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            self.ready = True
        except Exception as e:
            self.error = str(e); self._run_pyttsx3(); return

        while not self._stop_flag:
            while not self._stop_flag:
                with self._lock: text = self._queue.pop(0) if self._queue else None
                if text is None: break
                self._utt_len = max(0.5, len(text.split()) / (self._rate / 60.0))
                self._utt_start = time.time()
                self._tts_begin()
                try:
                    speaker.Rate   = int((self._rate - 175) / 25)
                    speaker.Volume = int(self._volume * 100)
                    speaker.Speak(text, 1)
                    while speaker.IsSpeaking():
                        if self._stop_flag: speaker.Speak("", 2); break
                        time.sleep(0.05)
                except: pass
                self._tts_end()
            self._event.wait(timeout=0.1); self._event.clear()

    def _run_linux(self):
        espeak = None
        for cmd in ["espeak-ng", "espeak"]:
            try:
                subprocess.run([cmd, "--version"], capture_output=True, timeout=2)
                espeak = cmd; break
            except: pass
        if espeak is None: self._run_pyttsx3(); return
        self.ready = True
        while not self._stop_flag:
            while not self._stop_flag:
                with self._lock: text = self._queue.pop(0) if self._queue else None
                if text is None: break
                self._utt_len = max(0.5, len(text.split()) / (self._rate / 60.0))
                self._utt_start = time.time()
                self._tts_begin()
                try:
                    subprocess.run([espeak, "-s", str(self._rate),
                                    "-a", str(int(self._volume * 200)), text])
                except: pass
                self._tts_end()
            self._event.wait(timeout=0.1); self._event.clear()

    def _run_pyttsx3(self):
        if not PYTTSX3_OK: self.error = "pyttsx3 not installed"; return
        # pyttsx3's runAndWait() pumps a COM/Windows message loop.
        # Running it on a shared thread can leak WM_QUIT into pygame.
        # Each utterance is isolated in its own daemon thread with CoInitialize.
        self.ready = True
        while not self._stop_flag:
            while not self._stop_flag:
                with self._lock: text = self._queue.pop(0) if self._queue else None
                if text is None: break
                self._utt_len = max(0.5, len(text.split()) / (self._rate / 60.0))
                self._utt_start = time.time()
                self._tts_begin()
                done = threading.Event()
                def _speak_isolated(t=text, r=self._rate, v=self._volume, d=done):
                    try:
                        if platform.system() == "Windows":
                            try:
                                import pythoncom
                                pythoncom.CoInitialize()
                            except Exception:
                                pass
                        import pyttsx3 as _p
                        eng = _p.init()
                        eng.setProperty("rate", r)
                        eng.setProperty("volume", v)
                        eng.say(t)
                        eng.runAndWait()
                        eng.stop()
                    except Exception:
                        pass
                    finally:
                        d.set()
                t = threading.Thread(target=_speak_isolated, daemon=True)
                t.start()
                done.wait(timeout=30)
                self._tts_end()
            self._event.wait(timeout=0.1); self._event.clear()

    def _level_loop(self):
        while not self._stop_flag:
            if self.speaking:
                el = time.time() - self._utt_start
                dur = self._utt_len
                ru, rd = self._RAMP_UP, self._RAMP_DOWN
                if   el < ru:          base = (el / ru) * self._SUSTAIN
                elif el < dur - rd:    ph = el*7.3; base = self._SUSTAIN + 0.06*math.sin(ph) + 0.04*math.sin(ph*2.1)
                elif el < dur:         base = (1.0 - (el-(dur-rd))/rd) * self._SUSTAIN
                else:                  base = 0.0
                target = max(0.0, min(1.0, base))
            else:
                target = 0.0
            self._level += (target - self._level) * 0.35
            self.on_level(max(0.0, self._level))
            time.sleep(0.025)


# ── Command Log ────────────────────────────────────────────────────────────────

class CommandLog:
    MAX_ENTRIES = 200
    def __init__(self):
        self._entries: List[dict] = []; self._lock = threading.Lock()
    def add(self, text: str):
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            self._entries.append({"ts": ts, "text": text})
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[-self.MAX_ENTRIES:]
    def get_entries(self) -> List[dict]:
        with self._lock: return list(self._entries)
    def clear(self):
        with self._lock: self._entries.clear()


# ── Font Picker Dropdown ───────────────────────────────────────────────────────

class FontDropdown:
    ROW_H=26; MAX_ROWS=8; RADIUS=6; SCROLL_SPD=3

    def __init__(self, fonts, font_list, current, on_select=None):
        self.fonts=fonts; self.font_list=font_list; self.current=current
        self.on_select=on_select or (lambda n: None)
        self.open=False; self._scroll=0; self._hover_idx=-1
        self._header_rect=None; self._list_rect=None
        self._clamp_scroll_to_selection()

    def layout(self, x, y, w):
        self._header_rect = pygame.Rect(x, y, w, self.ROW_H+4)
        self._list_rect   = pygame.Rect(x, y+self._header_rect.height,
                                        w, min(len(self.font_list), self.MAX_ROWS)*self.ROW_H)

    @property
    def total_height(self):
        h = self._header_rect.height if self._header_rect else self.ROW_H+4
        if self.open: h += min(len(self.font_list), self.MAX_ROWS)*self.ROW_H
        return h

    def handle_mousedown(self, pos) -> bool:
        if self._header_rect and self._header_rect.collidepoint(pos):
            self.open = not self.open; return True
        if self.open and self._list_rect and self._list_rect.collidepoint(pos):
            idx = self._item_at(pos)
            if 0 <= idx < len(self.font_list):
                self.current = self.font_list[idx]; self.on_select(self.current); self.open = False
            return True
        return False

    def handle_mousemove(self, pos):
        self._hover_idx = self._item_at(pos) if (self.open and self._list_rect
                                                   and self._list_rect.collidepoint(pos)) else -1

    def handle_mousewheel(self, pos, dy) -> bool:
        if self.open and self._list_rect and self._list_rect.collidepoint(pos):
            self._scroll = max(0, min(len(self.font_list)-self.MAX_ROWS, self._scroll-dy*self.SCROLL_SPD))
            return True
        return False

    def close(self): self.open = False

    def draw(self, surface, alpha=1.0):
        if not self._header_rect: return
        a = int(255*alpha); hdr = self._header_rect
        draw_rounded_rect_alpha(surface, hdr, (20,30,48,int(a*0.92)),
            border_rgba=(*BLUE_MID,a) if self.open else (*BLUE_DARK,int(a*0.8)),
            border_w=1, radius=self.RADIUS if not self.open else (self.RADIUS,self.RADIUS,0,0))
        arrow = self.fonts["sm"].render("▴" if self.open else "▾", True, (*BLUE_LITE, a))
        surface.blit(arrow, (hdr.x+8, hdr.y+(hdr.height-arrow.get_height())//2))
        dn = self.current if self.current else "-- default font --"
        ds = self.fonts["sm"].render(truncate_text(self.fonts["sm"], dn, hdr.width-arrow.get_width()-28),
                                     True, (*TEXT_BRIGHT, a))
        surface.blit(ds, (hdr.x+arrow.get_width()+14, hdr.y+(hdr.height-ds.get_height())//2))
        if not self.open: return
        lr = self._list_rect
        draw_rounded_rect_alpha(surface, lr, (10,16,28,int(a*0.97)),
            border_rgba=(*BLUE_MID,int(a*0.7)), border_w=1, radius=(0,0,self.RADIUS,self.RADIUS))
        for i in range(min(len(self.font_list), self.MAX_ROWS)):
            idx = self._scroll+i
            if idx >= len(self.font_list): break
            name = self.font_list[idx]; iy = lr.y+i*self.ROW_H
            ir   = pygame.Rect(lr.x+1, iy, lr.width-2, self.ROW_H)
            issel = (name == self.current); ishov = (idx == self._hover_idx)
            if issel:   draw_rounded_rect_alpha(surface, ir, (*BLUE_MID,int(a*0.35)), radius=0)
            elif ishov: draw_rounded_rect_alpha(surface, ir, (*BLUE_DARK,int(a*0.5)), radius=0)
            col = TEXT_BOLD if issel else (TEXT_BRIGHT if ishov else TEXT_DIM)
            ls  = self.fonts["sm"].render(truncate_text(self.fonts["sm"], name, lr.width-24), True, (*col, a))
            surface.blit(ls, (lr.x+10, iy+(self.ROW_H-ls.get_height())//2))
            if issel:
                tk = self.fonts["sm"].render("v", True, (*BLUE_LITE, a))
                surface.blit(tk, (lr.right-tk.get_width()-8, iy+(self.ROW_H-tk.get_height())//2))
        total = len(self.font_list)
        if total > self.MAX_ROWS:
            th = lr.height-4; tb = max(16, int(th*self.MAX_ROWS/total))
            ty = int((self._scroll/max(1,total-self.MAX_ROWS))*(th-tb))+lr.y+2
            draw_rounded_rect_alpha(surface, pygame.Rect(lr.right-6,lr.y+2,4,th), (*BLUE_DARK,int(a*0.4)), radius=2)
            draw_rounded_rect_alpha(surface, pygame.Rect(lr.right-6,ty,4,tb), (*BLUE_MID,a), radius=2)

    def _item_at(self, pos):
        if not self._list_rect: return -1
        return self._scroll+(pos[1]-self._list_rect.y)//self.ROW_H

    def _clamp_scroll_to_selection(self):
        if not self.current or self.current not in self.font_list:
            self._scroll = 0; return
        self._scroll = max(0, min(self.font_list.index(self.current), len(self.font_list)-self.MAX_ROWS))


# ── Dictation Popup ────────────────────────────────────────────────────────────

class DictationPopup:
    FADE_SPD=8.0; HOLD_T=0.6

    def __init__(self, screen_w, screen_h, fonts, on_submit=None, circle_cy=None, circle_r=None):
        self.sw=screen_w; self.sh=screen_h; self.fonts=fonts
        self.on_submit = on_submit or (lambda t: None)
        self.circle_cx = screen_w//2
        self.circle_cy = circle_cy if circle_cy is not None else screen_h//2
        self.circle_r  = circle_r  if circle_r  is not None else int(screen_w*0.13)
        self._state="hidden"; self._alpha=0.0
        self._text=""; self._hold_t=0.0; self._cursor_t=0.0

    def update_position(self, cy, r): self.circle_cy=cy; self.circle_r=r

    @property
    def visible(self): return self._state != "hidden"

    def show(self, partial_text=""):
        if self._state in ("hidden","fading"): self._state="listening"; self._alpha=0.0
        self._text = partial_text

    def update_partial(self, text):
        if self._state == "hidden": self.show(text)
        else: self._text=text; self._state="listening"

    def finalize(self, text): self._text=text; self._state="confirming"; self._hold_t=0.0
    def dismiss(self): self._state="fading"

    def submit_now(self):
        if self._text.strip(): self.on_submit(self._text.strip())
        self._state = "fading"

    def handle_key(self, key) -> bool:
        if not self.visible: return False
        if key == pygame.K_ESCAPE:                      self.dismiss();    return True
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER): self.submit_now(); return True
        return False

    def update(self, dt):
        self._cursor_t += dt
        if   self._state == "listening":
            self._alpha = min(1.0, self._alpha + self.FADE_SPD*dt)
        elif self._state == "confirming":
            self._alpha = min(1.0, self._alpha + self.FADE_SPD*dt)
            self._hold_t += dt
            if self._hold_t >= self.HOLD_T:
                if self._text.strip(): self.on_submit(self._text.strip())
                self._state = "fading"
        elif self._state == "fading":
            self._alpha = max(0.0, self._alpha - self.FADE_SPD*dt)
            if self._alpha <= 0.0: self._state="hidden"; self._text=""

    def _chord_w(self, r, dy):
        d2 = r*r-dy*dy; return int(math.sqrt(max(0,d2))) if d2>0 else 0

    def _wrap_into_circle(self, font, text, r, uhh):
        words=text.split(); lines=[]; lh=font.get_linesize(); row=0; cur=""
        while words:
            dy=-uhh+int(row*lh)+lh//2; hw=self._chord_w(r,dy)-12
            if hw<20: row+=1; continue
            w=words[0]; cand=(cur+" "+w).strip() if cur else w
            if font.size(cand)[0] <= hw*2: cur=cand; words.pop(0)
            else:
                if cur: lines.append((cur,hw)); cur=""; row+=1
                else: lines.append((truncate_text(font,w,hw*2),hw)); words.pop(0); row+=1
        if cur:
            dy=-uhh+int(row*lh)+lh//2; hw=self._chord_w(r,dy)-12
            lines.append((cur, max(hw,20)))
        return lines

    def draw(self, surface):
        if self._state=="hidden" or self._alpha<0.01: return
        a=int(self._alpha*255); cx=self.circle_cx; cy=self.circle_cy; r=self.circle_r
        font=self.fonts["md_b"]; sf=self.fonts["sm"]; lh=font.get_linesize()
        bot_res=sf.get_height()+14; uhh=(r-bot_res-8-((-r+14)))//2
        bc_y=cy+(-r+14+r-bot_res-8)//2
        draw_circle_alpha(surface, (0,0,0,int(self._alpha*80)), (cx,cy), r-2)
        rl=self._wrap_into_circle(font,self._text,r,uhh) if self._text else []
        n=len(rl); th=n*lh
        if n==0: ay=cy-lh//2
        else:
            cy_=bc_y-th//2; ta=cy+(-r+14)
            fr=min(1.0,th/max(1,uhh*2)); ay=int(cy_+(ta-cy_)*fr)
        for i,(lt,_) in enumerate(rl):
            fade=max(0.0,min(1.0,(i-(n-3))/2.0))
            ls=font.render(lt, True, (*lerp_col(TEXT_DIM,TEXT_BOLD,fade),a))
            surface.blit(ls, (cx-font.size(lt)[0]//2, ay+i*lh))
        if rl and self._state=="listening":
            lt,_=rl[-1]; lx=cx-font.size(lt)[0]//2
            if int(self._cursor_t*2)%2==0:
                cx2=lx+font.size(lt)[0]+3
                draw_rounded_rect_alpha(surface, pygame.Rect(cx2,ay+(n-1)*lh+3,2,lh-6),
                    (*MEDIA_LITE, a), radius=1)
        if not rl:
            ps=font.render("Listening...", True, (*TEXT_DIM, int(a*0.6)))
            surface.blit(ps, (cx-ps.get_width()//2, cy-ps.get_height()//2))
        dy2=cy+r-bot_res+4
        if self._state=="listening":
            pulse=0.5+0.5*math.sin(self._cursor_t*6.0)
            dc=lerp_col(MEDIA_MID, MEDIA_LITE, pulse)
        else: dc=GREEN_LITE
        draw_circle_alpha(surface, (*dc, int(a*0.35)), (cx,dy2), 10)
        draw_circle_alpha(surface, (*dc, a),           (cx,dy2), 5)
        slbl={"listening":"LISTENING","confirming":"PROCESSING","fading":""}.get(self._state,"")
        if slbl:
            sl=sf.render(slbl, True, (*TEXT_DIM, int(a*0.7)))
            surface.blit(sl, (cx-sl.get_width()//2, dy2+8))


# ── Wallpaper Browser ──────────────────────────────────────────────────────────

class WallpaperBrowser:
    THUMB_W=200; THUMB_H=120; THUMB_PAD=16; COLS=5
    SEARCH_DIRS=[
        os.path.expanduser("~/Pictures/wallpapers"),
        os.path.expanduser("~/Wallpapers"),
        os.path.expanduser("~/wallpapers"),
        os.path.expanduser("~/Pictures"),
        "/usr/share/backgrounds",
        "/usr/share/wallpapers",
        "/usr/share/pixmaps/backgrounds",
    ]

    def __init__(self, sw, sh, fonts, on_select=None, bar_h=None):
        self.sw=sw; self.sh=sh; self.fonts=fonts
        self._bar_h = bar_h or BAR_HEIGHT
        self.on_select=on_select or (lambda p: None)
        self.visible=False; self._images=[]; self._thumbs={}
        self._selected=0; self._scroll=0; self._loading=False
        self._alpha=0.0; self._fade_in=False
        tw=self.COLS*(self.THUMB_W+self.THUMB_PAD)-self.THUMB_PAD
        self._grid_x=(sw-tw)//2; self._grid_y=self._bar_h+60

    def open(self):
        if self.visible: return
        self.visible=True; self._fade_in=True; self._alpha=0.0
        self._selected=0; self._scroll=0
        if not self._images: self._start_scan()

    def close(self): self.visible=False; self._fade_in=False

    def handle_key(self, key) -> bool:
        if not self.visible: return False
        n=len(self._images)
        if key==pygame.K_ESCAPE: self.close(); return True
        if key in (pygame.K_RETURN,pygame.K_KP_ENTER):
            if self._images: self.on_select(self._images[self._selected])
            self.close(); return True
        if key==pygame.K_RIGHT and self._selected<n-1: self._selected+=1; self._clamp(); return True
        if key==pygame.K_LEFT  and self._selected>0:   self._selected-=1; self._clamp(); return True
        if key==pygame.K_DOWN: self._selected=min(n-1,self._selected+self.COLS); self._clamp(); return True
        if key==pygame.K_UP:   self._selected=max(0,self._selected-self.COLS);   self._clamp(); return True
        return False

    def handle_click(self, pos) -> bool:
        if not self.visible: return False
        for i,rect in enumerate(self._thumb_rects()):
            if rect.collidepoint(pos) and i<len(self._images):
                if self._selected==i: self.on_select(self._images[i]); self.close()
                else: self._selected=i
                return True
        return False

    def update(self, dt):
        if not self.visible: return
        self._alpha=max(0.0,min(1.0,self._alpha+(1.0 if self._fade_in else 0.0-self._alpha)*10.0*dt))
        self._load_pending_thumbs()

    def draw(self, surface):
        if not self.visible or self._alpha<0.01: return
        a=int(self._alpha*255)
        ov=pygame.Surface((self.sw,self.sh),pygame.SRCALPHA); ov.fill((5,8,15,int(a*0.92))); surface.blit(ov,(0,0))
        t=self.fonts["xl_b"].render("WALLPAPER BROWSER",True,(*TEXT_BRIGHT,a))
        surface.blit(t,(self.sw//2-t.get_width()//2,self._bar_h+16))
        h=self.fonts["sm"].render("Keys to select  *  ENTER to apply  *  ESC to close",True,(*TEXT_DIM,a))
        surface.blit(h,(self.sw//2-h.get_width()//2,self._bar_h+16+t.get_height()+4))
        if not self._images:
            ms=self.fonts["md"].render("Scanning..." if self._loading else "No images found",True,(*TEXT_DIM,a))
            surface.blit(ms,(self.sw//2-ms.get_width()//2,self.sh//2-ms.get_height()//2)); return
        for i,rect in enumerate(self._thumb_rects()):
            idx=self._scroll*self.COLS+i
            if idx>=len(self._images): break
            path=self._images[idx]; issel=(idx==self._selected)
            draw_rounded_rect_alpha(surface,rect,(*BLUE_MID,int(a*0.6)) if issel else (20,26,38,int(a*0.5)),
                border_rgba=(*BLUE_LITE,a) if issel else (*TEXT_DIM,a),border_w=2,radius=6)
            th=self._thumbs.get(path)
            if th:
                tw2,th2=th.get_size(); surface.blit(th,(rect.x+(rect.w-tw2)//2,rect.y+(rect.h-th2)//2))
            else:
                draw_rounded_rect_alpha(surface,pygame.Rect(rect.x+2,rect.y+2,rect.w-4,rect.h-4),(30,36,50,int(a*0.6)),radius=4)
                ls=self.fonts["sm"].render("loading...",True,(*TEXT_DIM,a))
                surface.blit(ls,(rect.x+rect.w//2-ls.get_width()//2,rect.y+rect.h//2-ls.get_height()//2))
            fn=os.path.basename(path); fn=fn[:18]+"..." if len(fn)>18 else fn
            fl=self.fonts["sm"].render(fn,True,(*TEXT_DIM,a))
            surface.blit(fl,(rect.x+rect.w//2-fl.get_width()//2,rect.y+rect.h-fl.get_height()-2))
        rows=math.ceil(len(self._images)/self.COLS)
        if rows>self._vrows():
            prog=self._scroll/max(1,rows-self._vrows()); bh=self.sh-self._grid_y-40
            draw_rounded_rect_alpha(surface,pygame.Rect(self.sw-8,self._grid_y+int(prog*(bh-40)),4,40),(*BLUE_MID,a),radius=2)

    def _vrows(self): return max(1,(self.sh-self._grid_y-self._bar_h-40)//(self.THUMB_H+self.THUMB_PAD))
    def _thumb_rects(self):
        rects=[]
        for row in range(self._vrows()):
            for col in range(self.COLS):
                rects.append(pygame.Rect(self._grid_x+col*(self.THUMB_W+self.THUMB_PAD),
                                         self._grid_y+row*(self.THUMB_H+self.THUMB_PAD),
                                         self.THUMB_W,self.THUMB_H))
        return rects
    def _clamp(self):
        row=self._selected//self.COLS; vr=self._vrows()
        if row<self._scroll: self._scroll=row
        elif row>=self._scroll+vr: self._scroll=row-vr+1
    def _start_scan(self):
        self._loading=True; threading.Thread(target=self._scan,daemon=True).start()
    def _scan(self):
        found=[]
        for d in self.SEARCH_DIRS:
            if not os.path.isdir(d): continue
            try:
                for root,dirs,files in os.walk(d):
                    dirs[:]=[x for x in dirs if not x.startswith(".")][:3]
                    for f in files:
                        if os.path.splitext(f)[1].lower() in IMG_EXT: found.append(os.path.join(root,f))
                        if len(found)>200: break
                    if len(found)>200: break
            except PermissionError: pass
        self._images=sorted(found); self._loading=False
    def _load_pending_thumbs(self):
        loaded=0; vr=self._vrows(); start=self._scroll*self.COLS
        for idx in range(start,min(len(self._images),start+vr*self.COLS+self.COLS)):
            if loaded>=2: break
            path=self._images[idx]
            if path not in self._thumbs:
                try:
                    img=pygame.image.load(path).convert(); tw,th=img.get_size()
                    scale=min((self.THUMB_W-4)/tw,(self.THUMB_H-20)/th)
                    self._thumbs[path]=pygame.transform.smoothscale(img,(max(1,int(tw*scale)),max(1,int(th*scale))))
                    loaded+=1
                except Exception: self._thumbs[path]=None


# ── Help panel ─────────────────────────────────────────────────────────────────

_HELP_VOICE_SECTIONS = [
    ("PLAYBACK", [
        ("play / resume",               "Start or resume playback"),
        ("pause",                        "Pause playback"),
        ("stop",                         "Stop playback"),
        ("next / skip",                  "Next track"),
        ("previous",                     "Previous track"),
        ("shuffle",                      "Toggle shuffle"),
        ("repeat / loop",                "Toggle repeat"),
        ("volume up / louder",           "Increase volume"),
        ("volume down / quieter",        "Decrease volume"),
        ("set volume <0-100>",           "Set exact volume level"),
    ]),
    ("SEARCH", [
        ("play <song name>",             "Play a specific song"),
        ("play <artist name>",           "Match artist, ask for song"),
        ("play anything by <artist>",    "Shuffle all tracks by artist"),
        ("open/change music folder",     "Browse for a media folder"),
    ]),
    ("UI & SYSTEM", [
        ("show/open settings",           "Open settings panel"),
        ("close/hide settings",          "Close settings panel"),
        ("set/browse wallpaper",         "Open wallpaper browser"),
        ("show help / open help",        "Show this help panel"),
        ("close help",                   "Close this panel"),
        ("stop listening / mute mic",    "Mute microphone"),
        ("start listening / unmute mic", "Unmute microphone"),
    ]),
]

_HELP_KEY_SECTIONS = [
    ("KEYBOARD", [
        ("F1",          "Toggle settings panel"),
        ("F2",          "Toggle command log panel"),
        ("CTRL+H",      "Toggle help panel"),
        ("CTRL+M",      "Toggle mic mute"),
        ("CTRL+W",      "Open wallpaper browser"),
        ("SPACE",       "Play / Pause"),
        ("RIGHT / LEFT","Next / Previous track"),
        ("UP / DOWN",   "Volume up / down"),
        ("S",           "Toggle shuffle"),
        ("R",           "Toggle repeat"),
        ("ESC",         "Quit application"),
    ]),
]


class HelpPanel:
    ANIM_SPEED=10.0; RADIUS=12; COL_PAD=28; ROW_H=22
    CAT_GAP=10; HDR_H=44; BOTTOM_PAD=32; SCROLL_SPD=3

    def __init__(self, sw: int, sh: int, fonts: dict, bar_h: int=None, panel_w: int=None):
        self.sw=sw; self.sh=sh; self.fonts=fonts
        self._bar_h = bar_h or BAR_HEIGHT
        self._panel_w = panel_w or 340
        self.open=False; self._t=0.0; self._scroll=0; self._max_scroll=0
        self._drag=False; self._panel_rect=None
        self._voice_rows=self._build_rows(_HELP_VOICE_SECTIONS)
        self._key_rows  =self._build_rows(_HELP_KEY_SECTIONS)
        self._content_h=0

    def toggle(self):           self.open = not self.open
    def set_open(self, v=None): self.open = (not self.open) if v is None else bool(v)

    def refresh_fonts(self, nf: dict): self.fonts=nf

    def handle_mousewheel(self, pos, dy) -> bool:
        if not self.open or self._panel_rect is None: return False
        if self._panel_rect.collidepoint(pos):
            self._scroll=max(0,min(self._max_scroll,self._scroll-dy*self.ROW_H*self.SCROLL_SPD))
            return True
        return False

    def handle_mousedown(self, pos) -> bool:
        if not self.open or self._panel_rect is None: return False
        return self._panel_rect.collidepoint(pos)

    def update(self, dt: float):
        target = 1.0 if self.open else 0.0
        self._t=max(0.0,min(1.0,self._t+(target-self._t)*self.ANIM_SPEED*dt))
        if not self.open: self._scroll=0

    def draw(self, surface, panel_alpha: float=1.0):
        if self._t<0.005: self._panel_rect=None; return
        anim=self._t; a_eff=anim*panel_alpha; ai=int(255*a_eff)
        f=self.fonts; lh=self.ROW_H; pad=self.COL_PAD
        bh=self._bar_h; pw=self._panel_w
        ph=self.sh-bh*2-40
        # Slide in from RIGHT (mirror of settings which slides from left)
        x=int(self.sw + anim*(-(pw+20))); x=max(self.sw-pw-20, x)
        y=bh+20
        self._panel_rect=pygame.Rect(x,y,pw,ph)
        draw_rounded_rect_alpha(surface,self._panel_rect,(12,18,30,int(255*a_eff)),
            border_rgba=(*HELP_ACCENT,int(255*a_eff)),border_w=1,radius=self.RADIUS)
        # Header
        draw_rounded_rect_alpha(surface,pygame.Rect(x,y,pw,self.HDR_H),(18,32,50,int(255*a_eff)),
            border_rgba=(*HELP_ACCENT,int(255*a_eff)),border_w=0,radius=(self.RADIUS,self.RADIUS,0,0))
        icon_s=f["md_b"].render("?",True,(*HELP_ACCENT,ai))
        surface.blit(icon_s,(x+16,y+(self.HDR_H-icon_s.get_height())//2))
        title_s=f["md_b"].render("Help",True,(*HELP_HDR_COL,ai))
        surface.blit(title_s,(x+40,y+(self.HDR_H-title_s.get_height())//2))
        hint_s=f["sm"].render("CTRL+H / 'close help'",True,(*TEXT_DIM,int(ai*0.7)))
        surface.blit(hint_s,(x+pw-hint_s.get_width()-14,y+(self.HDR_H-hint_s.get_height())//2))
        sep_y=y+self.HDR_H
        pygame.draw.line(surface,(*HELP_ACCENT,int(80*a_eff)),(x+10,sep_y),(x+pw-10,sep_y),1)
        # Scrollable content — both columns stacked vertically to fit narrow panel
        content_area=pygame.Rect(x+1,sep_y+1,pw-2,ph-self.HDR_H-2)
        try: clip=surface.subsurface(content_area)
        except ValueError: return
        voice_h=self._col_height(self._voice_rows,lh); key_h=self._col_height(self._key_rows,lh)
        content_h=voice_h+key_h+lh; self._content_h=content_h
        self._max_scroll=max(0,content_h-(content_area.height))
        col_w=pw-pad*2
        base_y=self.BOTTOM_PAD//2-self._scroll
        self._draw_column(clip,f,self._voice_rows,pad,base_y,col_w,ai,lh,header="VOICE COMMANDS")
        self._draw_column(clip,f,self._key_rows,  pad,base_y+voice_h,col_w,ai,lh,header="KEYBOARD SHORTCUTS")
        if self._max_scroll>0:
            sb_h=content_area.height-8; tb_h=max(20,int(sb_h*content_area.height/max(1,content_h)))
            tb_y=int((self._scroll/max(1,self._max_scroll))*(sb_h-tb_h))+4
            pygame.draw.rect(clip,(*BLUE_DARK,int(ai*0.4)),(pw-8,4,4,sb_h),border_radius=2)
            pygame.draw.rect(clip,(*HELP_ACCENT,ai),       (pw-8,tb_y,4,tb_h),border_radius=2)

    @staticmethod
    def _build_rows(sections):
        rows=[]
        for cat,items in sections:
            rows.append(("cat",cat,""))
            for key,desc in items: rows.append(("row",key,desc))
        return rows

    def _col_height(self,rows,lh):
        h=0
        for kind,_,__ in rows: h+=lh+(self.CAT_GAP if kind=="cat" else 0)
        return h+self.BOTTOM_PAD

    def _draw_column(self,clip,f,rows,cx,base_y,col_w,ai,lh,header=""):
        y=base_y; key_w=col_w*44//100
        hs=f["sm_b"].render(header,True,(*HELP_HDR_COL,int(ai*0.9))); clip.blit(hs,(cx,y)); y+=lh+4
        pygame.draw.line(clip,(*HELP_ACCENT,int(60*ai/255)),(cx,y),(cx+col_w-4,y),1); y+=6
        for kind,key_text,desc_text in rows:
            if kind=="cat":
                y+=self.CAT_GAP
                cat_s=f["sm_b"].render(key_text,True,(*HELP_CAT_COL,int(ai*0.85))); clip.blit(cat_s,(cx,y)); y+=lh
            else:
                pill_rect=pygame.Rect(cx,y,key_w-4,lh-2)
                draw_rounded_rect_alpha(clip,pill_rect,(*BLUE_DARK,int(ai*0.55)),
                    border_rgba=(*HELP_KEY_COL,int(ai*0.35)),border_w=1,radius=3)
                ks=f["sm"].render(truncate_text(f["sm"],key_text,key_w-12),True,(*HELP_KEY_COL,ai))
                clip.blit(ks,(cx+5,y+(lh-2-ks.get_height())//2))
                ds=f["sm"].render(truncate_text(f["sm"],desc_text,col_w-key_w-6),True,(*HELP_VAL_COL,int(ai*0.85)))
                clip.blit(ds,(cx+key_w+4,y+(lh-ds.get_height())//2)); y+=lh


# ── Waveform Circle ────────────────────────────────────────────────────────────

class WaveformCircle:
    NUM_BARS=64; BAR_MIN=2; BAR_WIDTH=3
    _HALO_LAYERS=5; _HALO_FRINGE_PX=12; _HALO_FRINGE_ALPHA=70

    def __init__(self, cx, cy, radius):
        self.cx=cx; self.cy=cy; self.base_radius=radius
        self.BAR_MAX = max(10, int(radius * 0.175))  # half the original 0.35
        self.bar_heights=[0.0]*self.NUM_BARS
        self.tts_radius=0.0
        self._noise=[random.random() for _ in range(self.NUM_BARS)]
        self._phase=0.0; self._tts_smooth=0.0
        self._bar_trig=[]   # pre-computed trig cache

    def _rebuild_trig_cache(self):
        dpb = 360.0 / self.NUM_BARS
        hw  = self.BAR_WIDTH / 2
        self._bar_trig = []
        for i in range(self.NUM_BARS):
            ar  = math.radians(i * dpb - 90.0)
            pr2 = math.radians(i * dpb)
            self._bar_trig.append((
                math.cos(ar), math.sin(ar),
                hw * math.cos(pr2), hw * math.sin(pr2)
            ))

    def update(self, stt: float, tts: float, dt: float):
        self._phase+=dt*2.0
        for i in range(self.NUM_BARS):
            nv=math.sin(self._phase+self._noise[i]*math.pi*2)
            target=stt*(0.5+0.5*abs(nv)) if stt>0.01 else 0.0
            speed=18.0 if target>self.bar_heights[i] else 4.0
            self.bar_heights[i]=max(0.0,min(1.0,self.bar_heights[i]+(target-self.bar_heights[i])*speed*dt))
        attack=14.0 if tts>self._tts_smooth else 3.5
        self._tts_smooth=max(0.0,min(1.0,self._tts_smooth+(tts-self._tts_smooth)*attack*dt))
        self.tts_radius=self._tts_smooth*30.0

    def draw(self, surface):
        cx,cy,r=self.cx,self.cy,self.base_radius

        # Rebuild trig cache if needed
        if len(self._bar_trig) != self.NUM_BARS:
            self._rebuild_trig_cache()

        lv=self._tts_smooth
        if lv>0.01:
            fa=int(self._HALO_FRINGE_ALPHA*lv)
            for fi in range(3):
                draw_circle_alpha(surface,
                    (*lerp_col(TTS_HALO_OUTER,TTS_HALO_FRINGE,fi/2),int(fa*(0.35+0.65*fi/2))),
                    (cx,cy), r+self._HALO_FRINGE_PX-fi*4)
            draw_circle_alpha(surface,(*DARK_BG,fa),(cx,cy),r-1)
        if lv>0.01:
            inner_r=int(r*(1.0-lv))
            for layer in range(self._HALO_LAYERS):
                frac=layer/max(1,self._HALO_LAYERS-1)
                gap=max(1,(r-inner_r)//(self._HALO_LAYERS+1))
                l_inner=max(0,inner_r-layer*gap)
                l_alpha=int(110*lv*(1.0-frac*0.6))
                l_col=lerp_col(TTS_HALO_OUTER,TTS_HALO_INNER,frac)
                if r<=l_inner or l_alpha<1: continue
                tmp=_get_surf(r*2, r*2)
                pygame.draw.circle(tmp,(*l_col,l_alpha),(r,r),r)
                if l_inner>0: pygame.draw.circle(tmp,(0,0,0,0),(r,r),l_inner)
                surface.blit(tmp,(cx-r,cy-r))
        draw_circle_alpha(surface,(*DARK_BG,CIRCLE_ALPHA),(cx,cy),r)

        # ── Bars + outer connecting ring ─────────────────────────────────────
        # Collect the tip (x2,y2) of every bar so we can draw the connecting
        # circle through them all. The outer ring radius is r + BAR_MAX so it
        # sits at the tip of a fully-extended bar; shorter bars leave a gap
        # showing the ring "floating" just past the bar end.
        tip_pts = []
        for i in range(self.NUM_BARS):
            ca, sa, dx, dy = self._bar_trig[i]
            bh = self.BAR_MIN + self.bar_heights[i] * (self.BAR_MAX - self.BAR_MIN)
            x1 = cx + r  * ca;       y1 = cy + r  * sa
            x2 = cx + (r+bh) * ca;   y2 = cy + (r+bh) * sa
            tip_pts.append((x2, y2))
            pygame.draw.polygon(surface, WAVEFORM_WHITE,
                [(x1-dx, y1-dy),(x1+dx, y1+dy),
                 (x2+dx, y2+dy),(x2-dx, y2-dy)])

        # Outer ring — polyline connecting all bar tips so it pulses with the waveform
        if len(tip_pts) >= 3:
            pygame.draw.polygon(surface, WAVEFORM_WHITE, [(int(x), int(y)) for x,y in tip_pts], 1)


# ── Settings Panel ─────────────────────────────────────────────────────────────

class SettingsPanel:
    ANIM_SPEED=8.0; RADIUS=10; SLIDER_H=24; SLIDER_PAD=14
    OUTPUT_H=280; OUTPUT_HDR_H=36; OUTPUT_LINE_H=17
    FONT_PICKER_TOP_PAD=10; FONT_PICKER_BOT_PAD=8

    def __init__(self, w, h, fonts, stt_ref=None, settings=None,
                 on_settings_change=None, font_list=None, on_font_select=None,
                 command_log=None, bar_h=None, panel_w=None):
        self.w=w; self.h=h; self.fonts=fonts; self.open=False; self._t=0.0
        self._bar_h   = bar_h   or BAR_HEIGHT
        self._panel_w = panel_w or PANEL_W
        self._stt=stt_ref; self._settings=settings or {}
        self._on_settings_change=on_settings_change or (lambda: None)
        self._command_log=command_log
        self._slider_panel_dragging=self._slider_bar_dragging=self._slider_thresh_dragging=False
        self._panel_alpha_val=float(self._settings.get("panel_alpha",0.82))
        self._bar_alpha_val  =float(self._settings.get("bar_alpha",  0.12))
        self._threshold_val  =float(self._settings.get("mic_threshold",0.10))
        self._output_box_rect=None
        self._log_scroll=0; self._log_autoscroll=True
        self._slider_panel_rect=self._slider_bar_rect=self._slider_thresh_rect=None
        self._slider_panel_track=self._slider_bar_track=self._slider_thresh_track=None
        self._font_dropdown=None
        if font_list is not None:
            self._font_dropdown=FontDropdown(fonts,font_list,self._settings.get("ui_font",""),
                                             on_select=on_font_select or (lambda n: None))
        # Wake-word text box
        self._ww_text    = self._settings.get("wake_word", "computer")
        self._ww_focused = False
        self._ww_rect    = None   # set during draw
        self._ww_apply_rect = None
        self._date_toggle_rect = None
        self._update_btn_rect  = None
        self._update_status    = ""   # "", "checking", "updating", "done", "error"
        self._update_msg       = ""

    def refresh_fonts(self, nf):
        self.fonts=nf
        if self._font_dropdown: self._font_dropdown.fonts=nf

    @property
    def panel_alpha(self): return self._panel_alpha_val
    @property
    def bar_alpha(self):   return self._bar_alpha_val
    @property
    def threshold(self):   return self._threshold_val

    def set_open(self, v=None): self.open=(not self.open) if v is None else bool(v)
    def toggle(self):           self.open=not self.open

    def handle_mousedown(self, pos) -> bool:
        if not self.open: return False
        if self._font_dropdown and self._font_dropdown.handle_mousedown(pos): return True
        # Wake-word Apply button
        if self._ww_apply_rect and self._ww_apply_rect.collidepoint(pos):
            self._ww_focused = False
            self._settings["wake_word"] = self._ww_text.strip()
            save_settings(self._settings)
            self._on_settings_change()
            return True
        if self._date_toggle_rect and self._date_toggle_rect.collidepoint(pos):
            cur = bool(self._settings.get("show_date", 1.0))
            self._settings["show_date"] = 0.0 if cur else 1.0
            save_settings(self._settings)
            self._on_settings_change()
            return True
        if self._update_btn_rect and self._update_btn_rect.collidepoint(pos):
            if self._update_status not in ("checking", "updating"):
                self._run_update()
            return True
        # Wake-word text box — click focuses/unfocuses
        if self._ww_rect and self._ww_rect.collidepoint(pos):
            self._ww_focused = True; return True
        else:
            self._ww_focused = False
        if self._slider_panel_rect and self._slider_panel_rect.collidepoint(pos):
            self._slider_panel_dragging=True; self._update_panel_slider_from_x(pos[0]); return True
        if self._slider_bar_rect and self._slider_bar_rect.collidepoint(pos):
            self._slider_bar_dragging=True; self._update_bar_slider_from_x(pos[0]); return True
        if self._slider_thresh_rect and self._slider_thresh_rect.collidepoint(pos):
            self._slider_thresh_dragging=True; self._update_thresh_slider_from_x(pos[0]); return True
        return False

    def handle_mousemove(self, pos):
        if self._font_dropdown: self._font_dropdown.handle_mousemove(pos)
        if self._slider_panel_dragging:    self._update_panel_slider_from_x(pos[0])
        elif self._slider_bar_dragging:    self._update_bar_slider_from_x(pos[0])
        elif self._slider_thresh_dragging: self._update_thresh_slider_from_x(pos[0])

    def handle_mouseup(self, pos):
        if self._slider_panel_dragging or self._slider_bar_dragging or self._slider_thresh_dragging:
            save_settings(self._settings)
        self._slider_panel_dragging=self._slider_bar_dragging=self._slider_thresh_dragging=False

    def handle_mousewheel(self, pos, dy) -> bool:
        if self._font_dropdown and self.open:
            if self._font_dropdown.handle_mousewheel(pos, dy): return True
        if self.open and self._output_box_rect and self._output_box_rect.collidepoint(pos):
            self._log_scroll=max(0, self._log_scroll-dy*self.OUTPUT_LINE_H*2)
            self._log_autoscroll=False; return True
        return False

    def _update_panel_slider_from_x(self, mx):
        tr=self._slider_panel_track or self._slider_panel_rect
        if not tr: return
        t=max(0.0,min(1.0,(mx-tr.x)/max(1,tr.width)))
        self._panel_alpha_val=PANEL_ALPHA_MIN+t*(PANEL_ALPHA_MAX-PANEL_ALPHA_MIN)
        self._settings["panel_alpha"]=self._panel_alpha_val; self._on_settings_change()

    def _update_bar_slider_from_x(self, mx):
        tr=self._slider_bar_track or self._slider_bar_rect
        if not tr: return
        t=max(0.0,min(1.0,(mx-tr.x)/max(1,tr.width)))
        self._bar_alpha_val=BAR_ALPHA_MIN+t*(BAR_ALPHA_MAX-BAR_ALPHA_MIN)
        self._settings["bar_alpha"]=self._bar_alpha_val; self._on_settings_change()

    def _update_thresh_slider_from_x(self, mx):
        tr=self._slider_thresh_track or self._slider_thresh_rect
        if not tr: return
        t=max(0.0,min(1.0,(mx-tr.x)/max(1,tr.width)))
        self._threshold_val=MIC_THRESH_MIN+t*(MIC_THRESH_MAX-MIC_THRESH_MIN)
        self._settings["mic_threshold"]=self._threshold_val
        if self._stt: self._stt._open_threshold=self._threshold_val
        self._on_settings_change()

    def handle_keydown(self, key, unicode_char) -> bool:
        """Returns True if the key was consumed by the wake-word text box."""
        if not self.open or not self._ww_focused: return False
        if key == pygame.K_BACKSPACE:
            self._ww_text = self._ww_text[:-1]
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            self._ww_focused = False
        elif unicode_char and unicode_char.isprintable() and len(self._ww_text) < 32:
            self._ww_text += unicode_char.lower()
        self._settings["wake_word"] = self._ww_text.strip()
        save_settings(self._settings)
        self._on_settings_change()
        return True

    def _run_update(self):
        """Pull latest code from git (or re-clone) in a background thread."""
        import subprocess, shutil
        self._update_status = "checking"
        self._update_msg    = "Checking for updates..."
        def _do():
            script_dir = os.path.dirname(os.path.abspath(__file__))
            git_dir    = os.path.join(script_dir, ".git")
            try:
                if os.path.isdir(git_dir):
                    # Already a git repo — just pull
                    self._update_status = "updating"
                    self._update_msg    = "Pulling latest changes..."
                    r = subprocess.run(
                        ["git", "-C", script_dir, "pull", "--ff-only"],
                        capture_output=True, text=True, timeout=60)
                    if r.returncode == 0:
                        out = r.stdout.strip()
                        self._update_msg = out if out else "Already up to date."
                    else:
                        self._update_msg = (r.stderr.strip() or r.stdout.strip() or "git pull failed")[:80]
                        self._update_status = "error"; return
                else:
                    # Not a git repo — clone into a temp dir then copy files over
                    self._update_status = "updating"
                    self._update_msg    = "Cloning repository..."
                    import tempfile
                    tmp = tempfile.mkdtemp(prefix="waveplayer_update_")
                    r = subprocess.run(
                        ["git", "clone", "--depth=1", REPO_URL, tmp],
                        capture_output=True, text=True, timeout=120)
                    if r.returncode != 0:
                        self._update_msg = (r.stderr.strip() or "Clone failed")[:80]
                        self._update_status = "error"
                        shutil.rmtree(tmp, ignore_errors=True); return
                    self._update_msg = "Copying files..."
                    for item in os.listdir(tmp):
                        if item in (".git", ".venv"): continue
                        src = os.path.join(tmp, item)
                        dst = os.path.join(script_dir, item)
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src, dst)
                    shutil.rmtree(tmp, ignore_errors=True)
                    self._update_msg = "Update complete. Restart to apply."
                # Re-install dependencies if requirements.txt was updated
                req = os.path.join(script_dir, "requirements.txt")
                venv_pip = os.path.join(script_dir, ".venv",
                    "Scripts" if platform.system()=="Windows" else "bin", "pip")
                if os.path.isfile(req) and os.path.isfile(venv_pip):
                    self._update_msg = "Updating dependencies..."
                    subprocess.run([venv_pip, "install", "-q", "-r", req],
                                   capture_output=True, timeout=120)
                self._update_status = "done"
                if "Already up to date" not in self._update_msg and "complete" not in self._update_msg:
                    self._update_msg = "Done! Restart to apply changes."
            except FileNotFoundError:
                self._update_status = "error"
                self._update_msg    = "git not found. Install git and retry."
            except subprocess.TimeoutExpired:
                self._update_status = "error"
                self._update_msg    = "Timed out. Check your connection."
            except Exception as e:
                self._update_status = "error"
                self._update_msg    = str(e)[:80]
        threading.Thread(target=_do, daemon=True).start()

    def update(self, dt):
        t=1.0 if self.open else 0.0
        self._t=max(0.0,min(1.0,self._t+(t-self._t)*self.ANIM_SPEED*dt))
        if not self.open:
            if self._font_dropdown: self._font_dropdown.close()
            self._ww_focused = False

    def draw(self, surface, panel_alpha=1.0):
        if self._t<0.005: return
        anim=self._t; a_eff=anim*panel_alpha
        pw=self._panel_w; bh=self._bar_h
        ph=self.h-bh*2-40
        x=int(-pw-20 + anim*(pw+20)); x=min(20, x)
        y=bh+20
        draw_rounded_rect_alpha(surface,pygame.Rect(x,y,pw,ph),(15,20,30,int(255*a_eff)),
            border_rgba=(*BLUE_MID,int(255*a_eff)),border_w=1,radius=self.RADIUS)
        ts=self.fonts["md_b"].render("Settings",True,BLUE_LITE); surface.blit(ts,(x+16,y+12))
        pygame.draw.line(surface,BLUE_MID,(x+10,y+34),(x+pw-10,y+34),1)

        fpr=0
        if self._font_dropdown:
            dp_x=x+self.SLIDER_PAD; dp_w=pw-self.SLIDER_PAD*2; dp_y=y+44
            self._font_dropdown.layout(dp_x,dp_y,dp_w)
            self._font_dropdown.draw(surface,alpha=a_eff)
            fpr=self.FONT_PICKER_TOP_PAD+self._font_dropdown.total_height+self.FONT_PICKER_BOT_PAD

        iy=y+44+fpr
        # ── Wake-word text box ─────────────────────────────────────────────
        ai=int(255*a_eff); pad=self.SLIDER_PAD
        lbl=self.fonts["sm"].render("WAKE WORD",True,(*TEXT_DIM,ai))
        surface.blit(lbl,(x+pad, iy))
        hint=self.fonts["sm"].render("(empty = always listen)",True,(*TEXT_DIM,int(ai*0.45)))
        surface.blit(hint,(x+pad+lbl.get_width()+6, iy))
        iy+=lbl.get_height()+4
        # Text box + Apply button side by side
        btn_w=46; gap=4
        box_h=26; box_rect=pygame.Rect(x+pad, iy, pw-pad*2-btn_w-gap, box_h)
        self._ww_rect=box_rect
        brd_col=(*BLUE_LITE,ai) if self._ww_focused else (*BLUE_DARK,int(ai*0.8))
        draw_rounded_rect_alpha(surface,box_rect,(20,28,46,int(ai*0.92)),border_rgba=brd_col,border_w=1,radius=5)
        cursor=""
        if self._ww_focused and int(time.time()*2)%2==0: cursor="|"
        disp_text=self._ww_text+cursor
        disp_col=TEXT_BRIGHT if self._ww_text else TEXT_DIM
        ts2=self.fonts["sm"].render(disp_text if disp_text.strip() else "— no wake word —",True,(*disp_col,ai))
        surface.blit(ts2,(box_rect.x+8, box_rect.y+(box_h-ts2.get_height())//2))
        # Apply button
        apply_rect=pygame.Rect(box_rect.right+gap, iy, btn_w, box_h)
        self._ww_apply_rect=apply_rect
        draw_rounded_rect_alpha(surface,apply_rect,(*MEDIA_MID,int(ai*0.8)),
            border_rgba=(*MEDIA_ACCENT,ai),border_w=1,radius=5)
        apls=self.fonts["sm"].render("SET",True,(*MEDIA_ACCENT,ai))
        surface.blit(apls,(apply_rect.centerx-apls.get_width()//2,apply_rect.centery-apls.get_height()//2))
        iy+=box_h+10
        # ── Show date toggle ──────────────────────────────────────────
        show_date = bool(self._settings.get("show_date", 1.0))
        tog_lbl = self.fonts["sm"].render("SHOW DATE", True, (*TEXT_DIM, ai))
        surface.blit(tog_lbl, (x+pad, iy))
        tog_w=42; tog_h=20; tog_x=x+pw-pad-tog_w; tog_y=iy
        tog_rect=pygame.Rect(tog_x,tog_y,tog_w,tog_h); self._date_toggle_rect=tog_rect
        tog_bg=(*MEDIA_MID,int(ai*0.9)) if show_date else (*BLUE_DARK,int(ai*0.7))
        tog_brd=(*MEDIA_ACCENT,ai) if show_date else (*BLUE_MID,int(ai*0.6))
        draw_rounded_rect_alpha(surface,tog_rect,tog_bg,border_rgba=tog_brd,border_w=1,radius=tog_h//2)
        tog_col=(*MEDIA_ACCENT,ai) if show_date else (*TEXT_DIM,ai)
        tog_txt=self.fonts["sm"].render("ON" if show_date else "OFF",True,tog_col)
        surface.blit(tog_txt,(tog_rect.centerx-tog_txt.get_width()//2,tog_rect.centery-tog_txt.get_height()//2))
        iy+=tog_h+8
        # ── Update button ─────────────────────────────────────────────
        busy = self._update_status in ("checking","updating")
        done = self._update_status == "done"
        err  = self._update_status == "error"
        upd_lbl = self.fonts["sm_b"].render(
            "Updating..." if busy else ("Updated!" if done else ("Error" if err else "Check for Updates")),
            True,
            (*TEXT_DIM,ai) if busy else ((*MEDIA_ACCENT,ai) if done else ((*RED_LITE,ai) if err else (*BLUE_LITE,ai))))
        upd_h=24; upd_w=pw-pad*2
        upd_rect=pygame.Rect(x+pad,iy,upd_w,upd_h); self._update_btn_rect=upd_rect
        upd_bg=(*BLUE_DARK,int(ai*0.5)) if busy else ((*MEDIA_DARK,int(ai*0.7)) if done else ((*RED_MID,int(ai*0.5)) if err else (*BLUE_DARK,int(ai*0.8))))
        upd_brd=(*TEXT_DIM,int(ai*0.4)) if busy else ((*MEDIA_MID,ai) if done else ((*RED_LITE,int(ai*0.7)) if err else (*BLUE_MID,ai)))
        draw_rounded_rect_alpha(surface,upd_rect,upd_bg,border_rgba=upd_brd,border_w=1,radius=upd_h//2)
        surface.blit(upd_lbl,(upd_rect.centerx-upd_lbl.get_width()//2,upd_rect.centery-upd_lbl.get_height()//2))
        iy+=upd_h+2
        # Status message
        if self._update_msg:
            msg_col=(*RED_LITE,ai) if err else (*TEXT_DIM,ai)
            # Wrap long messages across two lines
            max_w=pw-pad*2
            words=self._update_msg.split(); lines=[]; cur=""
            for w in words:
                test=(cur+" "+w).strip()
                if self.fonts["sm"].size(test)[0]<=max_w: cur=test
                else:
                    if cur: lines.append(cur)
                    cur=w
            if cur: lines.append(cur)
            for line in lines[:2]:
                ms=self.fonts["sm"].render(line,True,msg_col)
                surface.blit(ms,(x+pad,iy)); iy+=ms.get_height()+1
        iy+=4
        # sliders (drawn first so log clips above them)
        self._draw_sliders(surface, x, y, pw, ph, a_eff)
        # command log box — sits above the sliders
        slider_rows=3; lh_s=self.fonts["sm"].get_height()
        slider_block=(self.SLIDER_H+lh_s+6+14)*slider_rows+20
        log_bottom=y+ph-slider_block-8
        log_avail=max(0, log_bottom-iy)
        self._draw_log_section(surface, x, iy, pw, log_avail, a_eff)

    def _draw_log_section(self, surface, px, py, pw, ph, a):
        if ph < 40: return   # not enough room
        ai=int(255*a); PAD=10; hdr_h=self.OUTPUT_HDR_H
        out_h=max(0, ph-hdr_h-8)   # fill available space
        if out_h < 20: return
        box_y=py; box_x=px+PAD; box_w=pw-PAD*2
        draw_rounded_rect_alpha(surface,pygame.Rect(box_x,box_y,box_w,hdr_h),
            (10,20,35,int(ai*0.7)),border_rgba=(*MEDIA_MID,int(ai*1.0)),border_w=1,radius=(self.RADIUS,self.RADIUS,0,0))
        pygame.draw.line(surface,(*MEDIA_LITE,ai),(box_x+2,box_y+hdr_h-1),(box_x+box_w-4,box_y+hdr_h-1),1)
        ls=self.fonts["sm_b"].render("CMDS",True,(*MEDIA_LITE,ai)); surface.blit(ls,(box_x+8,box_y+(hdr_h-ls.get_height())//2))
        rect_y=box_y+hdr_h
        box_rect=pygame.Rect(box_x,rect_y,box_w,out_h); self._output_box_rect=box_rect
        draw_rounded_rect_alpha(surface,box_rect,(6,10,18,int(ai*0.92)),
            border_rgba=(*BLUE_DARK,int(ai*0.8)),border_w=1,radius=(0,0,6,6))
        font=self.fonts["mono_sm"]; ipad=8; mtw=box_w-ipad*2-6
        lines_meta=[]
        if self._command_log:
            entries=self._command_log.get_entries()
            if not entries: lines_meta.append(("No commands yet.",TEXT_DIM))
            else:
                for entry in entries:
                    tsw=font.size(entry["ts"]+"  ")[0]
                    wr=_wrap_text(font,entry["text"],mtw-tsw)
                    for i,ln in enumerate(wr):
                        lines_meta.append((entry["ts"]+"  "+ln if i==0 else "          "+ln,
                                           CMD_LOG_TEXT_COL if i==0 else TEXT_MID_C))
        lh=self.OUTPUT_LINE_H; total_h=len(lines_meta)*lh
        if self._log_autoscroll: self._log_scroll=max(0,total_h-out_h+ipad*2)
        max_sc=max(0,total_h-out_h+ipad*2); self._log_scroll=min(self._log_scroll,max_sc)
        clip_inner=pygame.Rect(box_x+1,rect_y+1,box_w-2,out_h-2)
        try: clip_surf=surface.subsurface(clip_inner)
        except ValueError: return
        text_y=ipad-self._log_scroll
        for i,(lt,bc) in enumerate(lines_meta):
            ty=text_y+i*lh
            if ty+lh<0: continue
            if ty>out_h: break
            clip_surf.blit(font.render(lt,True,(*bc,ai)),(ipad,ty))
        if total_h>out_h:
            sbw=3; sbx=box_w-sbw-3; trh=out_h-4
            tbh=max(20,int(trh*out_h/total_h)); tby=int((self._log_scroll/max(1,max_sc))*(trh-tbh))+2
            pygame.draw.rect(clip_surf,(*BLUE_DARK,int(ai*0.5)),(sbx,2,sbw,trh),border_radius=2)
            pygame.draw.rect(clip_surf,(*BLUE_MID,ai),(sbx,tby,sbw,tbh),border_radius=2)

    def _draw_sliders(self, surface, px, py, pw, ph, a):
        ai=int(255*a); tx=px+self.SLIDER_PAD; tw=pw-self.SLIDER_PAD*2; th=4; tr=7
        lh=self.fonts["sm"].get_height(); rh=lh+6+self.SLIDER_H+14; by=py+ph-rh*3-16
        def _row(lbl, val_t, norm, ry):
            ls=self.fonts["sm"].render(lbl,True,(*TEXT_DIM,ai)); surface.blit(ls,(px+self.SLIDER_PAD,ry))
            vs=self.fonts["sm"].render(val_t,True,(*BLUE_LITE,ai)); surface.blit(vs,(px+pw-vs.get_width()-self.SLIDER_PAD,ry))
            ty2=ry+lh+6; tr2=pygame.Rect(tx,ty2+(self.SLIDER_H-th)//2,tw,th)
            draw_rounded_rect_alpha(surface,tr2,(40,55,80,ai),radius=2)
            fw=max(4,int(tw*norm)); draw_rounded_rect_alpha(surface,pygame.Rect(tr2.x,tr2.y,fw,th),(*BLUE_MID,ai),radius=2)
            tcx=tr2.x+fw; tcy=tr2.centery
            draw_circle_alpha(surface,(*BLUE_LITE,ai),(tcx,tcy),tr)
            draw_circle_alpha(surface,(200,230,255,ai),(tcx,tcy),tr-3)
            hit_rect  =pygame.Rect(tx-tr,ty2,tw+tr*2,self.SLIDER_H)
            track_rect=pygame.Rect(tx,   ty2,tw,     self.SLIDER_H)
            return hit_rect, track_rect
        t1=(self._panel_alpha_val-PANEL_ALPHA_MIN)/max(1e-6,PANEL_ALPHA_MAX-PANEL_ALPHA_MIN)
        self._slider_panel_rect, self._slider_panel_track = _row("PANEL TRANSPARENCY",f"{int(t1*100)}%",t1,by)
        t2=(self._bar_alpha_val-BAR_ALPHA_MIN)/max(1e-6,BAR_ALPHA_MAX-BAR_ALPHA_MIN)
        self._slider_bar_rect,   self._slider_bar_track   = _row("BAR TRANSPARENCY",f"{int(t2*100)}%",t2,by+rh)
        t3=(self._threshold_val-MIC_THRESH_MIN)/max(1e-6,MIC_THRESH_MAX-MIC_THRESH_MIN)
        self._slider_thresh_rect, self._slider_thresh_track = _row("MIC THRESHOLD",f"{self._threshold_val:.3f}",t3,by+rh*2)


# ── Text wrap helper ───────────────────────────────────────────────────────────

def _wrap_text(font, text: str, max_w: int) -> list:
    if not text.strip(): return ["Mic Listening..."]
    lines=[]
    for para in text.split("\n"):
        words=para.split()
        if not words: lines.append(""); continue
        cur=""
        for word in words:
            test=(cur+" "+word).strip()
            if font.size(test)[0]<=max_w: cur=test
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
    return lines if lines else ["Mic Listening..."]


# ── Status bars ────────────────────────────────────────────────────────────────

_mode_pill_rect:       Optional[pygame.Rect] = None
_settings_btn_rect:    Optional[pygame.Rect] = None
_fullscreen_btn_rect:  Optional[pygame.Rect] = None
_playlist_btn_rect:    Optional[pygame.Rect] = None
_stats_rect:           Optional[pygame.Rect] = None
_mute_pill_rect:       Optional[pygame.Rect] = None

def draw_top_bar(surface, fonts, status, w, h, bar_alpha=1.0, bar_h=None):
    bh = bar_h or BAR_HEIGHT
    bba=int(255*bar_alpha); bg=MEDIA_BAR_BG
    bs=pygame.Surface((w,bh),pygame.SRCALPHA); bs.fill((*bg,bba)); surface.blit(bs,(0,0))
    dn=fonts["md"].render(APP_NAME,True,TEXT_BRIGHT); surface.blit(dn,(16,(bh-dn.get_height())//2))
    show_date = bool(getattr(status, "show_date", True))
    pad = max(2, int(bh * 0.12))
    if show_date:
        gap = max(1, int(bh * 0.04))
        avail = bh - pad * 2 - gap
        time_sz = max(8, int(avail * 0.60))
        date_sz = max(7, int(avail * 0.40))
        tf = pygame.font.Font(_FONT_PATH_BOLD, time_sz)
        df = pygame.font.Font(_FONT_PATH_REG,  date_sz)
        ts = tf.render(status.time_str, True, TEXT_BRIGHT)
        ds = df.render(status.date_str, True, TEXT_BRIGHT)
        ty = pad
        surface.blit(ts,(w//2-ts.get_width()//2, ty))
        surface.blit(ds,(w//2-ds.get_width()//2, ty+ts.get_height()+gap))
    else:
        # Fill most of the bar height, then truly centre the rendered surface
        time_sz = max(8, int(bh * 0.72))
        tf = pygame.font.Font(_FONT_PATH_BOLD, time_sz)
        ts = tf.render(status.time_str, True, TEXT_BRIGHT)
        surface.blit(ts,(w//2-ts.get_width()//2, (bh-ts.get_height())//2))
    ver_s=fonts["sm"].render(f"v{VERSION}",True,TEXT_DIM)
    surface.blit(ver_s,(w-ver_s.get_width()-16,(bh-ver_s.get_height())//2))

def draw_bottom_bar(surface, fonts, status, w, h, bar_alpha=1.0, bar_h=None):
    global _mode_pill_rect, _settings_btn_rect, _fullscreen_btn_rect, _playlist_btn_rect, _stats_rect, _mute_pill_rect
    bh = bar_h or BAR_HEIGHT
    bba=int(255*bar_alpha); by=h-bh
    bg=MEDIA_BAR_BG
    bs=pygame.Surface((w,bh),pygame.SRCALPHA); bs.fill((*bg,bba)); surface.blit(bs,(0,by))
    # ── Settings button (replaces mode pill) ──────────────────────────────────
    px2,py2=10,4
    set_lbl=fonts["sm_b"].render("⚙  Settings",True,TEXT_BRIGHT)
    set_pw=set_lbl.get_width()+px2*2; set_ph=set_lbl.get_height()+py2*2
    set_x=10; set_y=by+(bh-set_ph)//2
    set_rect=pygame.Rect(set_x,set_y,set_pw,set_ph); _settings_btn_rect=set_rect
    draw_rounded_rect_alpha(surface,set_rect,(20,35,55,200),border_rgba=(*BLUE_MID,180),border_w=1,radius=set_ph//2)
    surface.blit(set_lbl,(set_x+px2,set_y+py2))
    # ── Fullscreen toggle button ───────────────────────────────────────────────
    is_fs=bool(pygame.display.get_surface().get_flags() & pygame.FULLSCREEN)
    fs_str="W  Windowed" if is_fs else "F  Fullscreen"
    fs_lbl=fonts["sm_b"].render(fs_str,True,TEXT_BRIGHT)
    fs_pw=fs_lbl.get_width()+px2*2; fs_ph=fs_lbl.get_height()+py2*2
    fs_x=set_x+set_pw+8; fs_y=by+(bh-fs_ph)//2
    fs_rect=pygame.Rect(fs_x,fs_y,fs_pw,fs_ph); _fullscreen_btn_rect=fs_rect
    draw_rounded_rect_alpha(surface,fs_rect,(20,35,55,200),border_rgba=(*BLUE_MID,180),border_w=1,radius=fs_ph//2)
    surface.blit(fs_lbl,(fs_x+px2,fs_y+py2))
    # ── Playlist button ───────────────────────────────────────────────────────
    pl_lbl=fonts["sm_b"].render("=  Playlist",True,TEXT_BRIGHT)
    pl_pw=pl_lbl.get_width()+px2*2; pl_ph=pl_lbl.get_height()+py2*2
    pl_x=fs_x+fs_pw+8; pl_y=by+(bh-pl_ph)//2
    pl_rect=pygame.Rect(pl_x,pl_y,pl_pw,pl_ph); _playlist_btn_rect=pl_rect
    draw_rounded_rect_alpha(surface,pl_rect,(20,35,55,200),border_rgba=(*BLUE_MID,180),border_w=1,radius=pl_ph//2)
    surface.blit(pl_lbl,(pl_x+px2,pl_y+py2))
    # Wake-word active indicator
    if getattr(status, "wake_active", False):
        wlbl = fonts["sm_b"].render("◉ LISTENING", True, MEDIA_ACCENT)
        wpw  = wlbl.get_width() + px2*2; wph = wlbl.get_height() + py2*2
        wpx  = pill_x - wpw - 10; wpy = by + (bh - wph) // 2
        draw_rounded_rect_alpha(surface, pygame.Rect(wpx, wpy, wpw, wph),
            (*MEDIA_DARK, 210), border_rgba=(*MEDIA_MID, 200), border_w=1, radius=wph//2)
        surface.blit(wlbl, (wpx + px2, wpy + py2))
    # TTS indicator
    if status.tts_level>0.05:
        pulse=0.5+0.5*math.sin(time.time()*8.0)
        tc=lerp_col(BLUE_MID,BLUE_LITE,pulse*status.tts_level)
        tl=fonts["sm_b"].render("* TTS",True,tc)
        tpw=tl.get_width()+px2*2; tph=tl.get_height()+py2*2
        tpx=w-tpw-16; tpy=by+(bh-tph)//2
        draw_rounded_rect_alpha(surface,pygame.Rect(tpx,tpy,tpw,tph),
            (*BLUE_DARK,200),border_rgba=(*BLUE_MID,180),border_w=1,radius=tph//2)
        surface.blit(tl,(tpx+px2,tpy+py2))
    # Track status in centre always
    act=status.media_status[:60] if status.media_status else ""
    ma=fonts["sm"].render(act,True,MEDIA_ACCENT)
    surface.blit(ma,(w//2-ma.get_width()//2,by+(bh-ma.get_height())//2))
    # System stats with battery (throttled)
    if PSUTIL_OK:
        try:
            now_t = time.time()
            if now_t - getattr(status, "_stats_t", 0) >= 2.0:
                # per-cpu=True gives load per core; we show the peak core
                # so a single busy core shows high rather than diluted across all cores
                per_core = psutil.cpu_percent(interval=None, percpu=True)
                cpu_avg  = sum(per_core) / len(per_core)   # overall average
                cpu_peak = max(per_core)                    # busiest core
                ram  = psutil.virtual_memory()
                ram_used_gb = ram.used  / (1024**3)
                ram_total_gb = ram.total / (1024**3)
                ram_pct = ram.percent
                # this process's own CPU (sum across threads, so can exceed 100% on multi-core)
                try:
                    proc_cpu = getattr(status, "_proc", None)
                    if proc_cpu is None:
                        import os as _os
                        status._proc = psutil.Process(_os.getpid())
                        status._proc.cpu_percent(interval=None)  # prime it
                        proc_cpu = status._proc
                    self_cpu = proc_cpu.cpu_percent(interval=None)
                except Exception:
                    self_cpu = 0.0
                bat_str = ""
                try:
                    bat = psutil.sensors_battery()
                    if bat is not None:
                        pct = int(bat.percent)
                        chg = "+" if bat.power_plugged else "-"
                        bat_str = f"  BAT {pct}%{chg}"
                except Exception: pass
                # Show: sys avg / peak core / this app / RAM used
                ram_free_gb = (ram.total - ram.used) / (1024**3)
                status._stats_str = (
                    f"CPU {cpu_peak:.0f}%  "
                    f"RAM {ram_used_gb:.1f}GB / {ram_free_gb:.1f}GB free"
                    f"{bat_str}"
                )
                status._stats_t = now_t
            st = getattr(status, "_stats_str", "CPU --%  RAM --%")
        except Exception as e:
            st = f"stats err: {e}"
    else:
        st = "psutil not installed"
    ss=fonts["sm"].render(st,True,TEXT_BRIGHT)
    stats_pad=8; stats_vpad=3
    stats_x=w-ss.get_width()-stats_pad*2-16
    stats_y=by+(bh-ss.get_height())//2-stats_vpad
    _stats_rect_local=pygame.Rect(stats_x,stats_y,ss.get_width()+stats_pad*2,ss.get_height()+stats_vpad*2)
    _stats_rect=_stats_rect_local
    draw_rounded_rect_alpha(surface,_stats_rect_local,(20,35,55,160),border_rgba=(*BLUE_DARK,120),border_w=1,radius=_stats_rect_local.height//2)
    surface.blit(ss,(stats_x+stats_pad,by+(bh-ss.get_height())//2))
    # Mute pill — always visible, right side left of stats, clickable toggle
    global _mute_pill_rect
    if status.stt_muted:
        mic_lbl=fonts["sm_b"].render("MIC OFF",True,WHITE)
        mic_bg=(*RED_MID,210); mic_brd=(*RED_LITE,180)
    else:
        mic_lbl=fonts["sm_b"].render("MIC ON",True,MEDIA_ACCENT)
        mic_bg=(10,30,18,160); mic_brd=(*MEDIA_MID,140)
    mx2,my2=10,3; mw=mic_lbl.get_width()+mx2*2; mh=mic_lbl.get_height()+my2*2
    mute_x=stats_x-mw-8; mute_y=by+(bh-mh)//2
    mute_rect=pygame.Rect(mute_x,mute_y,mw,mh); _mute_pill_rect=mute_rect
    draw_rounded_rect_alpha(surface,mute_rect,mic_bg,border_rgba=mic_brd,border_w=1,radius=mh//2)
    surface.blit(mic_lbl,(mute_x+mx2,mute_y+my2))


# ── App status dataclass ───────────────────────────────────────────────────────

@dataclass
class AppStatus:
    time_str:     str   = "00:00"
    date_str:     str   = ""
    stt_level:    float = 0.0
    tts_level:    float = 0.0
    stt_ready:    bool  = False
    stt_muted:    bool  = False
    wake_active:  bool  = False
    media_status: str   = "No media"
    show_date:    bool  = True


# ── Vosk STT ───────────────────────────────────────────────────────────────────

class VoskSTT:

    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    MODEL_PATHS = [
        os.environ.get("VOSK_MODEL_PATH", ""),
        os.path.join(_SCRIPT_DIR, "models", "EN"),
        os.path.join(_SCRIPT_DIR, "models"),
        os.path.join(_SCRIPT_DIR, "vosk-model"),
        os.path.expanduser("~/models"),
        os.path.expanduser("~/vosk-model-small-en-us-0.15"),
        "/opt/vosk-model",
        os.path.join(os.getcwd(), "models", "EN"),
        os.path.join(os.getcwd(), "models"),
    ]

    def __init__(self, on_partial=None, on_final=None, on_level=None):
        self.on_partial=on_partial or (lambda r,s: None)
        self.on_final  =on_final   or (lambda t,r: None)
        self.on_level  =on_level   or (lambda v: None)
        self._running=False; self.ready=False; self.error=""; self.muted=False
        self._open_threshold=0.10

    def start(self):
        if not VOSK_OK: self.error="vosk not installed"; return
        self._running=True; threading.Thread(target=self._run,daemon=True).start()

    def stop(self): self._running=False


    def _find(self):
        for p in self.MODEL_PATHS:
            if not p or not os.path.isdir(p):
                continue
            # A valid Vosk model dir contains 'am' or 'conf' subdirs
            contents = os.listdir(p)
            if "am" in contents or "conf" in contents or "graph" in contents:
                return p
            # Maybe the path points to a parent — check one level down
            for sub in sorted(contents):
                sub_p = os.path.join(p, sub)
                if os.path.isdir(sub_p):
                    sub_contents = os.listdir(sub_p)
                    if "am" in sub_contents or "conf" in sub_contents or "graph" in sub_contents:
                        print(f"[VoskSTT] Found model in subdirectory: {sub_p}")
                        return sub_p
        print(f"[VoskSTT] Model not found. Searched:\n" +
              "\n".join(f"  {p}" for p in self.MODEL_PATHS if p))
        return None

    def _run(self):
        mp=self._find()
        if not mp: self.error="No Vosk model"; print("[VoskSTT] "+self.error); return
        try:
            import vosk; vosk.SetLogLevel(-1)
            model=vosk.Model(mp); rec=vosk.KaldiRecognizer(model,SAMPLE_RATE)
            rec.SetWords(True); self.ready=True; print(f"[VoskSTT] Ready -- {mp}")
        except Exception as e: self.error=str(e); print(f"[VoskSTT] {e}"); return

        def cb(indata,frames,ti,status):
            if not self._running: return
            rms=float(np.sqrt(np.mean(indata[:,0]**2))); self.on_level(min(1.0,rms*8.0))
            pcm=(indata[:,0]*32767).astype(np.int16).tobytes()
            if rec.AcceptWaveform(pcm):
                raw=json.loads(rec.Result()).get("text","").strip()
                if raw: self.on_final([],raw)
            elif not self.muted:
                raw=json.loads(rec.PartialResult()).get("partial","").strip()
                if raw: self.on_partial(raw,None)
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE,channels=1,dtype="float32",blocksize=4000,callback=cb):
                while self._running: time.sleep(0.05)
        except Exception as e: self.error=str(e); print(f"[VoskSTT] {e}")

    # ── static command parsers ─────────────────────────────────────────────────

    @staticmethod
    def mute_command(raw):
        low=raw.lower().strip()
        if any(c in low for c in MUTE_COMMANDS):   return "mute"
        if any(c in low for c in UNMUTE_COMMANDS): return "unmute"
        return None

    @staticmethod
    def wallpaper_command(raw): return any(c in raw.lower().strip() for c in WALLPAPER_COMMANDS)

    @staticmethod
    def settings_command(raw):
        low=raw.lower().strip()
        if any(c in low for c in SETTINGS_OPEN_CMDS):  return "open"
        if any(c in low for c in SETTINGS_CLOSE_CMDS): return "close"
        return None

    @staticmethod
    def playlist_command(raw):
        low=raw.lower().strip()
        if any(c in low for c in PLAYLIST_OPEN_CMDS):  return "open"
        if any(c in low for c in PLAYLIST_CLOSE_CMDS): return "close"
        return None

    @staticmethod
    def help_command(raw) -> Optional[str]:
        low=raw.lower().strip()
        if any(c in low for c in HELP_CLOSE_COMMANDS): return "close"
        if any(c in low for c in HELP_OPEN_COMMANDS):  return "open"
        return None

    @staticmethod
    def media_command(raw) -> Optional[str]:
        low=raw.lower().strip()
        # artist-shuffle prefix — must precede plain "play" check
        for prefix in MEDIA_PLAY_ARTIST_CMDS:
            if low.startswith(prefix):
                artist=low[len(prefix):].strip()
                if artist: return f"play_artist_all:{artist}"
        # play named
        if low.startswith("play "):
            tail=low[len("play "):].strip()
            if tail and tail not in ("music","media"):
                is_transport=any(low==c or low==c.rstrip() for c in MEDIA_PLAY_CMDS)
                if not is_transport: return f"play_named:{tail}"
        if any(c==low or low.startswith(c) for c in MEDIA_PLAY_CMDS):    return "play"
        if any(c==low for c in MEDIA_PAUSE_CMDS):   return "pause"
        if any(c==low for c in MEDIA_STOP_CMDS):    return "stop"
        if any(c==low for c in MEDIA_NEXT_CMDS):    return "next"
        if any(c==low for c in MEDIA_PREV_CMDS):    return "prev"
        if any(c==low for c in MEDIA_SHUFFLE_CMDS): return "shuffle"
        if any(c==low for c in MEDIA_REPEAT_CMDS):  return "repeat"
        if any(c==low or c in low for c in MEDIA_VOL_UP_CMDS): return "vol_up"
        if any(c==low or c in low for c in MEDIA_VOL_DN_CMDS): return "vol_dn"
        if any(c==low for c in MEDIA_FOLDER_CMDS):  return "folder"
        for prefix in ("set volume ","volume "):
            if low.startswith(prefix):
                tail=low[len(prefix):].strip()
                ones={"zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
                      "eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19}
                tens={"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90,"one hundred":100,"max":100}
                fixes={"forty":"forty","tree":"three","won":"one","to":"two","too":"two"}
                tail=fixes.get(tail,tail)
                def w2n(txt):
                    txt=txt.replace("-"," "); parts=txt.split()
                    if txt in ("hundred","one hundred"): return 100
                    if len(parts)==1:
                        if parts[0] in ones: return ones[parts[0]]
                        if parts[0] in tens: return tens[parts[0]]
                    if len(parts)==2 and parts[0] in tens and parts[1] in ones: return tens[parts[0]]+ones[parts[1]]
                    return None
                n=w2n(tail)
                if n is not None: return f"vol_set:{n/100}"
                try:
                    num=float(tail); return f"vol_set:{num/100 if num>1 else num}"
                except ValueError: pass
        return None


# ── Media scan ─────────────────────────────────────────────────────────────────

MEDIA_SEARCH_DIRS = [
    os.path.expanduser("~/Music"),
    os.path.expanduser("~/music"),
    os.path.expanduser("~/Videos"),
    os.path.expanduser("~/videos"),
    os.path.expanduser("~/Movies"),
]
_HOME_SKIP_DIRS = {
    "node_modules","__pycache__",".git","snap",".local",
    ".config",".cache",".mozilla",".thunderbird",".steam",
    "proc","sys","dev",
}

def _scan_media(dirs=None) -> list:
    home=os.path.expanduser("~"); found=[]; seen_real: set=set()
    for d in (dirs or MEDIA_SEARCH_DIRS):
        if not os.path.isdir(d): continue
        max_depth=5 if os.path.realpath(d)==os.path.realpath(home) else 8
        try:
            for root,dirs2,files in os.walk(d,followlinks=False):
                depth=root[len(d):].count(os.sep)
                if depth>=max_depth: dirs2[:]=[];continue
                dirs2[:]=[x for x in dirs2 if not x.startswith(".") and x not in _HOME_SKIP_DIRS]
                for f in files:
                    ext=os.path.splitext(f)[1].lower()
                    if ext in AUDIO_EXT or ext in VIDEO_EXT:
                        full=os.path.join(root,f); rp=os.path.realpath(full)
                        if rp not in seen_real: seen_real.add(rp); found.append(full)
                    if len(found)>2000: break
                if len(found)>2000: break
        except PermissionError: pass
    return sorted(found)


# ── np helper ──────────────────────────────────────────────────────────────────

def _np_to_pg(arr): return pygame.surfarray.make_surface(np.ascontiguousarray(arr.transpose(1,0,2)))


# ── Media Player Mode ──────────────────────────────────────────────────────────

class MediaPlayerMode:
    DUCK_VOLUME    = 0.20
    DUCK_SPEED     = 6.0
    NUM_INNER_BARS = 24
    CTRL_BTN_W     = 52
    CTRL_BTN_H     = 32
    CTRL_RADIUS    = 8
    VOL_BAR_H      = 24   # height of the clickable volume bar hit zone

    def __init__(self, cx: int, cy: int, circle_r: int, fonts: dict, layout: dict=None):
        self.cx=cx; self.cy=cy; self.r=circle_r; self.fonts=fonts
        ly = layout or {}
        # Scale button/bar sizes from layout if provided, else fall back to constants
        self.CTRL_BTN_W  = ly.get("btn_w",  self.CTRL_BTN_W)
        self.CTRL_BTN_H  = ly.get("btn_h",  self.CTRL_BTN_H)
        self.VOL_BAR_H   = ly.get("vol_bar_h", self.VOL_BAR_H)
        # Store title/time gaps for use in _draw_info_arc and _draw_controls
        self._title_gap  = ly.get("title_gap", int(circle_r * 0.45))
        self._time_gap   = ly.get("time_gap",  int(circle_r * 0.38))
        self._ctrl_gap   = ly.get("ctrl_gap",  int(circle_r * 0.18))
        self._bar_h      = ly.get("bar_h", 36)
        self._screen_h   = ly.get("screen_h", 0)
        self.tracks: list=[]; self.current_idx=0
        self.playing=True; self.paused=False; self.volume=0.7
        self.shuffle=False; self.repeat=False
        self._duck_target=1.0; self._duck_factor=1.0
        self._phase=0.0; self._bars=[0.0]*self.NUM_INNER_BARS; self._eq_phase=0.0
        self._art_surf=None; self._art_cache: dict={}
        self._video_cap=None; self._video_frame=None; self._video_lock=threading.Lock()
        self._video_thread=None; self._video_running=False
        self._video_path=None; self._video_fps=24.0; self._video_pos=0.0; self._video_duration=0.0
        self._start_t=0.0; self._position=0.0; self._duration=0.0
        self._btn_rects: list=[]; self._vol_bar_rect: Optional[pygame.Rect]=None; self.music_level: float=0.0
        self._seek_bar_rect: Optional[pygame.Rect]=None
        self._seek_bar_x: int = 0
        self._seek_bar_w: int = 1
        self.status_msg="Scanning for media…"
        self._pending_artist: Optional[str]=None; self._pending_artist_matches: list=[]
        self._current_folder: str = ""   # set when user picks a folder
        self._saved_playback: dict = load_playback()   # restored after scan
        threading.Thread(target=self._bg_scan,daemon=True).start()

    def refresh_fonts(self, fonts: dict): self.fonts=fonts

    def update_layout(self, cx: int, cy: int, r: int, layout: dict):
        """Update geometry for a new screen size without touching playback."""
        self.cx=cx; self.cy=cy; self.r=r
        ly=layout
        self.CTRL_BTN_W  = ly.get("btn_w",  self.CTRL_BTN_W)
        self.CTRL_BTN_H  = ly.get("btn_h",  self.CTRL_BTN_H)
        self.VOL_BAR_H   = ly.get("vol_bar_h", self.VOL_BAR_H)
        self._title_gap  = ly.get("title_gap", self._title_gap)
        self._time_gap   = ly.get("time_gap",  self._time_gap)
        self._ctrl_gap   = ly.get("ctrl_gap",  self._ctrl_gap)
        self._bar_h      = ly.get("bar_h",     self._bar_h)
        self._screen_h   = ly.get("screen_h",  self._screen_h)

    # ── media controls ─────────────────────────────────────────────────────────────

    def play(self):
        if not self.tracks: return
        path=self.tracks[self.current_idx]; ext=os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXT: self._play_video(path)
        else:                self._play_audio(path)

    def pause(self):
        if self._video_cap is not None and self._video_running:
            self._video_running=False; self.paused=True; pygame.mixer.music.pause(); self.status_msg="Paused"
        elif self.playing and not self.paused:
            pygame.mixer.music.pause(); self.paused=True; self._position=time.time()-self._start_t; self.status_msg="Paused"

    def resume(self):
        if self.paused:
            if self._video_cap is not None:
                self._video_running=True
                self._video_thread=threading.Thread(target=self._video_loop,daemon=True); self._video_thread.start()
            pygame.mixer.music.unpause(); self.paused=False; self.playing=True
            self._start_t=time.time()-self._position; self.status_msg=f"Playing: {self._track_name()}"

    def stop(self):
        self._stop_video(); pygame.mixer.music.stop()
        self.playing=False; self.paused=False; self._position=0.0; self._video_frame=None; self.status_msg="Stopped"

    def next_track(self):
        if not self.tracks: return
        was=self.playing or self.paused; self.stop()
        self.current_idx=(random.randrange(len(self.tracks)) if self.shuffle
                          else (self.current_idx+1)%len(self.tracks))
        self._resolve_art()
        if was: self.play()

    def prev_track(self):
        if not self.tracks: return
        was=self.playing or self.paused; self.stop()
        self.current_idx=(self.current_idx-1)%len(self.tracks); self._resolve_art()
        if was: self.play()

    def set_volume(self, v: float): self.volume=max(0.0,min(1.0,v)); self._apply_volume()
    def set_ducked(self, ducked: bool): self._duck_target=self.DUCK_VOLUME if ducked else 1.0

    def open_folder(self):
        def _pick():
            folder = None
            # ── Try tkinter first (works on Windows + Linux with Tk installed) ─
            if TK_OK:
                try:
                    root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True)
                    folder=filedialog.askdirectory(title="Select Media Folder"); root.destroy()
                except Exception: folder=None
            # ── Windows fallback: PowerShell folder picker ────────────────────
            if not folder and platform.system()=="Windows":
                try:
                    ps=(
                        "Add-Type -AssemblyName System.Windows.Forms;"
                        "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
                        "$f.Description='Select Media Folder';"
                        "if($f.ShowDialog() -eq 'OK'){Write-Output $f.SelectedPath}"
                    )
                    result=subprocess.run(
                        ["powershell","-NoProfile","-Command",ps],
                        capture_output=True,text=True,timeout=60)
                    p=result.stdout.strip()
                    if p and os.path.isdir(p): folder=p
                except Exception: pass
            # ── Linux fallback: zenity or kdialog ────────────────────────────
            if not folder and platform.system()=="Linux":
                for cmd in [["zenity","--file-selection","--directory","--title=Select Media Folder"],
                            ["kdialog","--getexistingdirectory",os.path.expanduser("~")]]:
                    try:
                        result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
                        p=result.stdout.strip()
                        if p and os.path.isdir(p): folder=p; break
                    except Exception: pass
            if folder:
                self.status_msg=f"Scanning {os.path.basename(folder)}…"
                found=_scan_media([folder])
                if found:
                    was=self.playing or self.paused; self.stop()
                    self.tracks=found; self.current_idx=0
                    self._current_folder=folder
                    self._resolve_art()
                    self.status_msg=f"Loaded {len(found)} files from {os.path.basename(folder)}"
                    # Persist folder immediately so next launch finds it
                    save_playback(
                        track_path=self.tracks[0] if self.tracks else "",
                        position=0.0, volume=self.volume,
                        shuffle=self.shuffle, repeat=self.repeat,
                        folder=folder)
                    if was: self.play()
                else: self.status_msg="No media found in that folder"
        threading.Thread(target=_pick,daemon=True).start()

    # ── fuzzy matching ────────────────────────────────────────────────────────

    @staticmethod
    def _word_set(text: str) -> set:
        import re; return set(re.sub(r"[^a-z0-9 ]"," ",text.lower()).split())

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        if a==b: return 0
        if not a: return len(b)
        if not b: return len(a)
        if len(a)>len(b): a,b=b,a
        cur=list(range(len(a)+1))
        for i,bc in enumerate(b):
            prev=cur[:]; cur[0]=i+1
            for j,ac in enumerate(a):
                cur[j+1]=prev[j] if bc==ac else 1+min(prev[j],prev[j+1],cur[j])
        return cur[len(a)]

    @staticmethod
    def _fuzzy_word_score(q_words: set, t_words: set) -> float:
        if not q_words or not t_words: return 0.0
        total=0.0
        for qw in q_words:
            best=max((1.0-(MediaPlayerMode._edit_distance(qw,tw)/max(len(qw),len(tw),1)) for tw in t_words),default=0.0)
            total+=best
        return total/len(q_words)

    def _path_dir_parts(self,path):
        parts=Path(path).parts; return [p.lower() for p in parts[max(0,len(parts)-3):-1]]

    def _artist_score(self,query:str,path:str)->float:
        q_words=self._word_set(query); dir_parts=self._path_dir_parts(path)
        best=0.0
        # Check folder/directory names (organised libraries)
        for part in dir_parts: best=max(best,self._fuzzy_word_score(q_words,self._word_set(part)))
        # Also check the filename itself — covers "Artist - Song.mp3" flat layouts
        fname=os.path.splitext(os.path.basename(path))[0]
        best=max(best,self._fuzzy_word_score(q_words,self._word_set(fname)))
        return best

    def _song_score(self,query:str,path:str)->float:
        name=os.path.splitext(os.path.basename(path))[0]
        # If filename looks like "Artist - Song Title", score only against the
        # song-title portion so artist queries don't masquerade as song matches.
        if " - " in name:
            name = name.split(" - ", 1)[1]
        return self._fuzzy_word_score(self._word_set(query),self._word_set(name))

    def find_tracks_by_artist(self,artist_query:str)->list:
        THRESH=0.45
        return [i for i,p in enumerate(self.tracks) if self._artist_score(artist_query,p)>=THRESH]

    def find_track_by_name(self,query:str)->Optional[int]:
        q_words=self._word_set(query); best_s=0.0; best_i=None
        for i,p in enumerate(self.tracks):
            s=self._song_score(query,p)
            if s>best_s: best_s,best_i=s,i
        return best_i if best_s>=0.55 else None

    def find_best_match(self,query:str)->dict:
        ARTIST_THRESH=0.45; SONG_THRESH=0.65; ARTIST_PREFER_COUNT=2
        a_scored=sorted(((self._artist_score(query,p),i) for i,p in enumerate(self.tracks)),reverse=True)
        best_artist_score=a_scored[0][0] if a_scored else 0.0
        artist_matches=[i for s,i in a_scored if s>=ARTIST_THRESH]
        best_song_score=0.0; best_song_idx=None
        for i,p in enumerate(self.tracks):
            s=self._song_score(query,p)
            if s>best_song_score: best_song_score,best_song_idx=s,i
        if best_artist_score>=ARTIST_THRESH:
            # Prefer artist result when:
            # - multiple tracks match (it's clearly an artist query), OR
            # - artist score beats song score, OR
            # - song score isn't confident enough
            if (len(artist_matches)>=ARTIST_PREFER_COUNT
                    or best_artist_score>=best_song_score
                    or best_song_score<SONG_THRESH):
                return {"type":"artist","artist_matches":artist_matches,"song_idx":None,
                        "artist_score":best_artist_score,"song_score":best_song_score}
        if best_song_score>=SONG_THRESH and best_song_idx is not None:
            return {"type":"song","artist_matches":artist_matches,"song_idx":best_song_idx,
                    "artist_score":best_artist_score,"song_score":best_song_score}
        return {"type":"none","artist_matches":artist_matches,"song_idx":best_song_idx,
                "artist_score":best_artist_score,"song_score":best_song_score}

    def play_artist_shuffle(self,artist_query:str,matches=None)->str:
        if matches is None: matches=self.find_tracks_by_artist(artist_query)
        if not matches: return f"No tracks found for {artist_query}"
        random.shuffle(matches)
        other=[i for i in range(len(self.tracks)) if i not in set(matches)]
        self.tracks=[self.tracks[i] for i in matches]+[self.tracks[i] for i in other]
        self.current_idx=0; self.shuffle=True; self._resolve_art(); self.play()
        return f"Shuffling {len(matches)} tracks by {artist_query.title()}"

    def play_specific_track(self,song_query:str,track_idx=None)->str:
        idx=track_idx if track_idx is not None else self.find_track_by_name(song_query)
        if idx is None: return f"Could not find {song_query}"
        self.current_idx=idx; self._resolve_art(); self.play()
        return f"Playing {os.path.splitext(os.path.basename(self.tracks[idx]))[0]}"

    # ── click / key ───────────────────────────────────────────────────────────

    def handle_click(self, pos) -> bool:
        # Seek bar checked FIRST — has priority over everything below it
        if self._seek_bar_rect and self._seek_bar_rect.collidepoint(pos):
            rel = max(0.0, min(1.0, (pos[0] - self._seek_bar_x) / max(1, self._seek_bar_w)))
            self.seek_to_frac(rel); return True
        # Volume bar
        if self._vol_bar_rect and self._vol_bar_rect.collidepoint(pos):
            rel = max(0.0, min(1.0, (pos[0] - self._vol_bar_rect.x) / max(1, self._vol_bar_rect.width)))
            self.set_volume(rel); return True
        # Transport buttons
        for label,rect in self._btn_rects:
            if rect.collidepoint(pos): self._btn_action(label); return True
        # Circle tap = play/pause
        dx,dy=pos[0]-self.cx,pos[1]-self.cy
        if dx*dx+dy*dy<=self.r*self.r:
            if self.playing and not self.paused: self.pause()
            elif self.paused: self.resume()
            else: self.play()
            return True
        return False

    def handle_key(self, key: int, mods: int):
        if key==pygame.K_SPACE:
            if self.playing and not self.paused: self.pause()
            elif self.paused: self.resume()
            else: self.play()
        elif key==pygame.K_RIGHT: self.next_track()
        elif key==pygame.K_LEFT:  self.prev_track()
        elif key==pygame.K_UP:    self.set_volume(self.volume+0.15)
        elif key==pygame.K_DOWN:  self.set_volume(self.volume-0.15)
        elif key==pygame.K_s:     self.shuffle=not self.shuffle
        elif key==pygame.K_r:     self.repeat=not self.repeat

    # ── update / draw ─────────────────────────────────────────────────────────

    def _get_music_level(self) -> float:
        if not self.playing or self.paused: return 0.0
        return min(1.0, sum(self._bars)/max(1,len(self._bars))*1.4)

    def update(self, dt: float, stt_active: bool):
        self._phase+=dt*3.0; self._eq_phase+=dt*4.0
        self._duck_factor+=(self._duck_target-self._duck_factor)*self.DUCK_SPEED*dt
        self._apply_volume()
        if self.playing and not self.paused and self._video_cap is None:
            self._position=time.time()-self._start_t
            if not pygame.mixer.music.get_busy() and self.playing:
                if self.repeat: self.play()
                else:           self.next_track()
        if self.playing and not self.paused:
            for i in range(self.NUM_INNER_BARS):
                # Gentler, more wave-like motion — lower peaks, slower freq
                t=(0.18+0.25*abs(math.sin(self._eq_phase*0.5+i*0.45))
                      +0.20*abs(math.sin(self._eq_phase*0.9+i*0.29))
                      +0.10*abs(math.sin(self._eq_phase*1.5+i*0.61)))
                t=min(0.75,t); speed=6.0 if t>self._bars[i] else 3.0
                self._bars[i]+=(t-self._bars[i])*speed*dt
        else:
            for i in range(self.NUM_INNER_BARS): self._bars[i]+=(0.0-self._bars[i])*5.0*dt
        self.music_level=self._get_music_level()

    def draw(self, surface, wave_circle: WaveformCircle, stt_active: bool,
             stt_level: float, tts_level: float, dt: float):
        cx,cy,r=self.cx,self.cy,self.r
        draw_circle_alpha(surface,(*MEDIA_DARK,220),(cx,cy),r)
        frame_drawn=False
        if self._video_cap is not None or self._video_frame is not None:
            with self._video_lock: vf=self._video_frame
            if vf: self._blit_circle_image(surface,vf,cx,cy,r); frame_drawn=True
        if not frame_drawn and self._art_surf: self._blit_circle_image(surface,self._art_surf,cx,cy,r)
        self._draw_inner_eq(surface,cx,cy,r)
        pygame.draw.circle(surface,MEDIA_ACCENT,(cx,cy),r,2)
        self._draw_info_arc(surface,cx,cy,r)
        self._draw_controls(surface,cx,cy,r)
        if stt_active and stt_level>0.005:
            ovl=pygame.Surface((r*2,r*2),pygame.SRCALPHA); ovl.fill((0,0,0,0))
            pygame.draw.circle(ovl,(*DARK_BG,160),(r,r),r); surface.blit(ovl,(cx-r,cy-r))
            wave_circle.update(max(stt_level,self.music_level*0.4),tts_level,dt)
            wave_circle.draw(surface)

    # ── internals ─────────────────────────────────────────────────────────────

    def _bg_scan(self):
        sv = self._saved_playback
        saved_folder = sv.get("folder", "") if sv else ""
        # Scan the saved custom folder first; fall back to defaults if empty/gone
        if saved_folder and os.path.isdir(saved_folder):
            tracks = _scan_media([saved_folder])
            if tracks:
                self._current_folder = saved_folder
            else:
                tracks = _scan_media()   # folder exists but empty — try defaults
        else:
            tracks = _scan_media()
        self.tracks = tracks
        if tracks:
            self.status_msg=f"Found {len(tracks)} media files"
            self._restore_saved_playback()   # must come before _resolve_art
            self._resolve_art()
        else:
            self.status_msg="No media found — use 'open music folder'"

    def _restore_saved_playback(self):
        """Called once after the initial scan. Seeks to the saved track/position."""
        sv = self._saved_playback
        if not sv or not sv.get("track"):
            # No save file — just start playing from track 0
            self.play(); return
        saved_path = sv["track"]
        # Restore volume / flags first (safe even if track not found)
        self.volume  = float(sv.get("volume",  self.volume))
        self.shuffle = bool(sv.get("shuffle", self.shuffle))
        self.repeat  = bool(sv.get("repeat",  self.repeat))
        # Find saved track in the current list
        try:
            idx = self.tracks.index(saved_path)
        except ValueError:
            # Track not found (removed/renamed) — fall back to track 0
            print(f"[Playback] Saved track not found, starting from beginning")
            self.play(); return
        self.current_idx = idx
        saved_pos = float(sv.get("position", 0.0))
        ext = os.path.splitext(saved_path)[1].lower()
        if ext in VIDEO_EXT:
            self._play_video(saved_path)   # video seek not supported, play from start
        else:
            self._play_audio(saved_path, start_pos=saved_pos)
        self.status_msg = f"♪ {os.path.basename(saved_path)}"

    def _track_name(self) -> str:
        if not self.tracks: return "—"
        return os.path.basename(self.tracks[self.current_idx])

    def _play_audio(self, path: str, start_pos: float = 0.0):
        self._stop_video()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self.volume*self._duck_factor)
            pygame.mixer.music.play(start=start_pos)
            self.playing=True; self.paused=False
            self._position=start_pos
            self._start_t=time.time()-start_pos
            self.status_msg=f"♪ {os.path.basename(path)}"
            # Get duration in background thread so UI doesn't stall
            def _fetch_dur(p):
                d = _get_audio_duration(p)
                self._duration = d
            threading.Thread(target=_fetch_dur, args=(path,), daemon=True).start()
        except Exception as e: self.status_msg=f"Cannot play: {e}"

    def _play_video(self, path: str):
        if not CV2_OK: self.status_msg="cv2 not available — cannot play video"; return
        self._stop_video()
        try:
            cap=cv2.VideoCapture(path)
            if not cap.isOpened(): self.status_msg=f"Cannot open: {os.path.basename(path)}"; return
            self._video_cap=cap; self._video_path=path
            self._video_fps=cap.get(cv2.CAP_PROP_FPS) or 24.0
            total_frames=cap.get(cv2.CAP_PROP_FRAME_COUNT)
            self._video_duration=total_frames/max(1,self._video_fps)
            self._video_pos=0.0; self._video_running=True; self.playing=True; self.paused=False
            self.status_msg=f"▶ {os.path.basename(path)}"
            try: pygame.mixer.music.load(path); pygame.mixer.music.set_volume(self.volume*self._duck_factor); pygame.mixer.music.play()
            except Exception: pass
            self._video_thread=threading.Thread(target=self._video_loop,daemon=True); self._video_thread.start()
        except Exception as e: self.status_msg=f"Video error: {e}"

    def _video_loop(self):
        if self._video_cap is None: return
        interval=1.0/max(1,self._video_fps)
        while self._video_running and self._video_cap is not None:
            t0=time.time(); ret,frame=self._video_cap.read()
            if not ret: self._video_running=False; break
            rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB); d=self.r*2; h,w=rgb.shape[:2]
            scale=min(d/w,d/h); nw,nh=int(w*scale),int(h*scale)
            if nw>0 and nh>0: rgb=cv2.resize(rgb,(nw,nh))
            surf=_np_to_pg(rgb)
            with self._video_lock: self._video_frame=surf
            self._video_pos+=interval; time.sleep(max(0.0,interval-(time.time()-t0)))
        if not self._video_running:
            if self.repeat: self.play()
            else:           self.next_track()

    def _stop_video(self):
        self._video_running=False
        if self._video_cap:
            try: self._video_cap.release()
            except Exception: pass
            self._video_cap=None
        with self._video_lock: self._video_frame=None

    def _apply_volume(self):
        eff=self.volume*self._duck_factor
        try: pygame.mixer.music.set_volume(eff)
        except Exception: pass

    def _resolve_art(self):
        if not self.tracks: return
        folder=os.path.dirname(self.tracks[self.current_idx])
        if folder in self._art_cache: self._art_surf=self._art_cache[folder]; return
        candidates=["cover.jpg","cover.jpeg","cover.png","folder.jpg","folder.jpeg",
                    "folder.png","artwork.jpg","artwork.png","front.jpg","front.png"]
        surf=None
        for name in candidates:
            p=os.path.join(folder,name)
            if os.path.isfile(p): surf=self._load_circle_image(p); break
        if surf is None:
            try:
                for f in sorted(os.listdir(folder)):
                    if os.path.splitext(f)[1].lower() in {".jpg",".jpeg",".png",".bmp"}:
                        surf=self._load_circle_image(os.path.join(folder,f))
                        if surf: break
            except Exception: pass
        self._art_cache[folder]=surf; self._art_surf=surf

    def _load_circle_image(self, path: str) -> Optional[pygame.Surface]:
        try: return pygame.image.load(path).convert()
        except Exception: return None

    def _blit_circle_image(self, surface, img_surf, cx, cy, r):
        d=r*2
        if d<=0: return
        iw,ih=img_surf.get_size()
        if iw<=0 or ih<=0: return
        if iw!=d or ih!=d:
            scale=max(d/iw,d/ih); nw,nh=max(1,int(iw*scale)),max(1,int(ih*scale))
            img_surf=pygame.transform.smoothscale(img_surf,(nw,nh))
            sw,sh=img_surf.get_size()
            if sw<d or sh<d:
                padded=pygame.Surface((max(sw,d),max(sh,d)),pygame.SRCALPHA); padded.fill((0,0,0,0))
                padded.blit(img_surf,((max(sw,d)-sw)//2,(max(sh,d)-sh)//2)); img_surf=padded
            ox=max(0,(img_surf.get_width()-d)//2); oy=max(0,(img_surf.get_height()-d)//2)
            cw=min(d,img_surf.get_width()-ox); ch=min(d,img_surf.get_height()-oy)
            if cw<=0 or ch<=0: return
            try: img_surf=img_surf.subsurface(pygame.Rect(ox,oy,cw,ch))
            except ValueError: return
            if cw!=d or ch!=d: img_surf=pygame.transform.smoothscale(img_surf,(d,d))
        out=pygame.Surface((d,d),pygame.SRCALPHA); out.fill((0,0,0,0))
        img_rgba=img_surf.convert_alpha() if img_surf.get_flags()&pygame.SRCALPHA==0 else img_surf.copy()
        out.blit(img_rgba,(0,0))
        mask=pygame.Surface((d,d),pygame.SRCALPHA); mask.fill((0,0,0,255))
        pygame.draw.circle(mask,(0,0,0,0),(r,r),r); out.blit(mask,(0,0),special_flags=pygame.BLEND_RGBA_SUB)
        dark=pygame.Surface((d,d),pygame.SRCALPHA); dark.fill((0,0,0,0))
        pygame.draw.circle(dark,(0,0,0,80),(r,r),r); out.blit(dark,(0,0))
        surface.blit(out,(cx-r,cy-r))

    def _draw_inner_eq(self, surface, cx, cy, r):
        n = self.NUM_INNER_BARS
        if not any(b > 0.005 for b in self._bars):
            return

        max_len = int(r * 0.28)   # shorter — less aggressive than before
        # Build smooth interpolated heights using cosine blending between bars
        STEPS = 4   # sub-steps between each bar for smoothness
        total_pts = n * STEPS
        smooth = []
        for i in range(total_pts):
            bar_i   = i // STEPS
            bar_next = (bar_i + 1) % n
            t = (i % STEPS) / STEPS
            # cosine interpolation
            t_cos = (1.0 - math.cos(t * math.pi)) / 2.0
            h = self._bars[bar_i] * (1.0 - t_cos) + self._bars[bar_next] * t_cos
            smooth.append(h)

        # Build outer and inner polygon point lists
        outer_pts = []
        inner_pts = []
        inner_r = max(4, r - 4)   # just inside the circle border
        for j, h in enumerate(smooth):
            angle = math.radians(j * 360.0 / total_pts - 90.0)
            dist_outer = inner_r - int(h * max_len)
            dist_inner = inner_r - max_len - 4
            outer_pts.append((cx + dist_outer * math.cos(angle),
                               cy + dist_outer * math.sin(angle)))
            inner_pts.append((cx + dist_inner * math.cos(angle),
                               cy + dist_inner * math.sin(angle)))

        # Draw filled wave band (outer ring → inner ring, fully closed)
        avg_level = sum(self._bars) / max(1, n)
        fill_alpha = int(60 + avg_level * 80)
        fill_surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        fill_surf.fill((0, 0, 0, 0))
        def to_local(pts): return [(x - (cx - r), y - (cy - r)) for x, y in pts]
        # Close the ring: outer forward + inner backward, then back to outer[0]
        outer_loc = to_local(outer_pts)
        inner_loc = to_local(inner_pts)
        poly = outer_loc + list(reversed(inner_loc)) + [outer_loc[0]]
        if len(poly) >= 3:
            fill_col = lerp_col(MEDIA_MID, MEDIA_ACCENT, avg_level)
            pygame.draw.polygon(fill_surf, (*fill_col, fill_alpha), poly)
        surface.blit(fill_surf, (cx - r, cy - r))

        # Draw a glowing line along the outer wave edge (closed loop)
        for j in range(total_pts):
            j2 = (j + 1) % total_pts
            p1 = (int(outer_pts[j][0]),  int(outer_pts[j][1]))
            p2 = (int(outer_pts[j2][0]), int(outer_pts[j2][1]))
            intensity = (smooth[j] + smooth[j2]) / 2.0
            line_col = lerp_col(MEDIA_MID, MEDIA_ACCENT, intensity)
            pygame.draw.line(surface, line_col, p1, p2, 1)

    def _draw_info_arc(self, surface, cx, cy, r):
        # Track time / status ABOVE the circle (replaces track name)
        if self.playing or self.paused:
            pos_s = f"{int(self._position//60)}:{int(self._position%60):02d}"
            ts = self.fonts["lg_b"].render(pos_s, True, MEDIA_ACCENT)
        else:
            pos_s = self.status_msg[:40]
            ts = self.fonts["md"].render(pos_s, True, TEXT_DIM)
        surface.blit(ts, (cx - ts.get_width()//2, cy - r - ts.get_height() - self._title_gap))

    def _draw_controls(self, surface, cx, cy, r):
        self._btn_rects=[]
        buttons=[("prev","⏮"),("play_pause","⏸" if (self.playing and not self.paused) else "▶"),
                 ("next","⏭"),("shuffle","⇌"),("repeat","↺"),("folder","📂")]
        total_w=len(buttons)*(self.CTRL_BTN_W+8)-8
        sx=cx-total_w//2

        # ── Layout: anchor group from bottom bar upward so nothing overlaps ───
        vol_lbl_h = self.fonts["sm"].get_height()
        seek_bar_h = 8
        seek_zone_h = seek_bar_h + 4 + vol_lbl_h + 10  # seek bar + time labels + gap
        group_h   = self.CTRL_BTN_H + 8 + self.VOL_BAR_H + 4 + vol_lbl_h
        full_h    = seek_zone_h + group_h
        if self._screen_h:
            group_bottom = self._screen_h - self._bar_h - 10
        else:
            pos_h = self.fonts["sm"].get_height()
            group_bottom = cy + r + self._time_gap + pos_h + self._ctrl_gap + full_h
        ctrl_y = group_bottom - group_h

        # ── Seek / timeline bar — sits above buttons ───────────────────────────
        self._draw_seek_bar(surface, cx - total_w//2, ctrl_y - seek_zone_h - 4, total_w, seek_bar_h)

        for i,(key,icon) in enumerate(buttons):
            bx=sx+i*(self.CTRL_BTN_W+8); rect=pygame.Rect(bx,ctrl_y,self.CTRL_BTN_W,self.CTRL_BTN_H)
            is_active=(key=="shuffle" and self.shuffle) or (key=="repeat" and self.repeat)
            bg=(*MEDIA_MID,180) if is_active else (*MEDIA_DARK,150)
            brd=(*MEDIA_ACCENT,200) if is_active else (*MEDIA_MID,150)
            draw_rounded_rect_alpha(surface,rect,bg,border_rgba=brd,border_w=1,radius=self.CTRL_RADIUS)
            ic=self.fonts["md"].render(icon,True,MEDIA_ACCENT if is_active else TEXT_MID_C)
            surface.blit(ic,(rect.centerx-ic.get_width()//2,rect.centery-ic.get_height()//2))
            self._btn_rects.append((key,rect))

        # ── Clickable volume bar ───────────────────────────────────────────────
        vol_y  = ctrl_y + self.CTRL_BTN_H + 8
        bar_w  = total_w; bar_h = 8; bar_x = cx - bar_w//2
        bar_r  = bar_h // 2
        # track (background)
        track_rect = pygame.Rect(bar_x, vol_y + (self.VOL_BAR_H - bar_h)//2, bar_w, bar_h)
        draw_rounded_rect_alpha(surface, track_rect, (*MEDIA_DARK, 200), radius=bar_r)
        # fill
        fill_w = max(bar_r*2, int(bar_w * self.volume))
        fill_rect = pygame.Rect(bar_x, track_rect.y, fill_w, bar_h)
        fill_col = lerp_col(MEDIA_MID, MEDIA_ACCENT, self.volume)
        draw_rounded_rect_alpha(surface, fill_rect, (*fill_col, 220), radius=bar_r)
        # thumb
        thumb_x = bar_x + fill_w; thumb_y = track_rect.centery
        draw_circle_alpha(surface, (*MEDIA_ACCENT, 240), (thumb_x, thumb_y), 7)
        draw_circle_alpha(surface, (220, 255, 230, 200), (thumb_x, thumb_y), 4)
        # label
        vol_pct = int(self.volume * 100)
        vols = self.fonts["sm"].render(f"Vol {vol_pct}%", True, TEXT_BRIGHT)
        surface.blit(vols, (cx - vols.get_width()//2, vol_y + self.VOL_BAR_H + 2))
        # store hit rect for click handling
        self._vol_bar_rect = pygame.Rect(bar_x - 4, vol_y, bar_w + 8, self.VOL_BAR_H)

    def _draw_seek_bar(self, surface, bar_x, top_y, total_w, bar_h):
        """Draw timeline seek bar. bar_x is the left edge."""
        if not self.tracks:
            return

        # ── Get position and duration ──────────────────────────────────────────
        if self._video_cap is not None:
            dur = self._video_duration
            pos = self._video_pos
        else:
            pos = self._position
            dur = getattr(self, '_duration', 0.0)
            if not dur or dur <= 0:
                try:
                    ms = pygame.mixer.music.get_pos()
                    dur = ms / 1000.0 if ms and ms > 0 else 0.0
                except Exception:
                    dur = 0.0

        # frac: 0.0 at start, 1.0 at end
        if dur > 0:
            frac = max(0.0, min(1.0, pos / dur))
        else:
            frac = 0.0

        bar_y = top_y
        bar_r = bar_h // 2

        # ── Background track ───────────────────────────────────────────────────
        draw_rounded_rect_alpha(surface,
            pygame.Rect(bar_x, bar_y, total_w, bar_h),
            (*MEDIA_DARK, 200), radius=bar_r)

        # ── Filled progress ────────────────────────────────────────────────────
        fill_w = int(total_w * frac)          # no minimum — 0 at start is correct
        if fill_w > 0:
            fill_col = lerp_col(MEDIA_MID, MEDIA_ACCENT, frac)
            draw_rounded_rect_alpha(surface,
                pygame.Rect(bar_x, bar_y, fill_w, bar_h),
                (*fill_col, 230), radius=bar_r)

        # ── Thumb ──────────────────────────────────────────────────────────────
        thumb_x = bar_x + fill_w
        thumb_y = bar_y + bar_h // 2
        draw_circle_alpha(surface, (*MEDIA_ACCENT, 255), (thumb_x, thumb_y), 8)
        draw_circle_alpha(surface, (220, 255, 230, 220), (thumb_x, thumb_y), 4)

        # ── Time labels ────────────────────────────────────────────────────────
        def _fmt(s):
            s = max(0, int(s))
            return f"{s // 60}:{s % 60:02d}"
        tl = self.fonts["sm"].render(_fmt(pos), True, TEXT_BRIGHT)
        tr = self.fonts["sm"].render(_fmt(dur), True, TEXT_BRIGHT)
        surface.blit(tl, (bar_x, bar_y + bar_h + 3))
        surface.blit(tr, (bar_x + total_w - tr.get_width(), bar_y + bar_h + 3))

        # ── Hit rect and coords for click/drag ─────────────────────────────────
        self._seek_bar_rect = pygame.Rect(bar_x, bar_y - 8, total_w, bar_h + 20)
        self._seek_bar_x    = bar_x
        self._seek_bar_w    = total_w

    def seek_to_frac(self, frac: float):
        """Seek to a fractional position 0.0–1.0 of the current track."""
        frac = max(0.0, min(1.0, frac))
        if self._video_cap is not None:
            try:
                target = frac * self._video_duration
                self._video_cap.set(1, int(target * self._video_fps))
                self._video_pos = target
            except Exception: pass
            return
        dur = self._duration
        if dur <= 0:
            return
        target_s = frac * dur
        try:
            was_paused = self.paused
            pygame.mixer.music.play(start=target_s)
            self._start_t  = time.time() - target_s
            self._position = target_s
            self.playing   = True
            self.paused    = False
            if was_paused:
                pygame.mixer.music.pause()
                self.paused = True
        except Exception: pass

    def _btn_action(self, label: str):
        if label=="prev":       self.prev_track()
        elif label=="play_pause":
            if self.playing and not self.paused: self.pause()
            elif self.paused: self.resume()
            else: self.play()
        elif label=="next":      self.next_track()
        elif label=="shuffle":   self.shuffle=not self.shuffle
        elif label=="repeat":    self.repeat=not self.repeat
        elif label=="folder":    self.open_folder()


# ── IPC helpers ────────────────────────────────────────────────────────────────

def _make_server_socket():
    cfg=IPC_CFG
    if cfg["mode"]=="unix":
        path=cfg["socket_path"]
        if os.path.exists(path): os.unlink(path)
        srv=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); srv.bind(path)
    else:
        srv=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        srv.bind((cfg["host"],cfg["port"]))
    return srv

def _cleanup_server_socket():
    cfg=IPC_CFG
    if cfg["mode"]=="unix" and os.path.exists(cfg["socket_path"]):
        os.unlink(cfg["socket_path"])

def send_msg(msg: dict):
    cfg=IPC_CFG
    try:
        s=(socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) if cfg["mode"]=="unix"
           else socket.socket(socket.AF_INET,socket.SOCK_STREAM))
        if cfg["mode"]=="unix": s.connect(cfg["socket_path"])
        else: s.connect((cfg["host"],cfg["port"]))
        s.sendall((json.dumps(msg)+"\n").encode()); s.close()
    except Exception as e: print(f"IPC: {e}")



# ── Playlist Panel ─────────────────────────────────────────────────────────────

class PlaylistPanel:
    ANIM_SPEED = 10.0
    ROW_H      = 26
    RADIUS     = 10
    SCROLL_SPD = 3

    def __init__(self, sw: int, sh: int, bar_h: int, fonts: dict, on_select=None):
        self.sw = sw; self.sh = sh; self.bar_h = bar_h; self.fonts = fonts
        self.on_select = on_select or (lambda i: None)
        self.open = False; self._t = 0.0
        self._tracks: list = []; self._current_idx = 0
        self._scroll = 0; self._hover = -1
        self._panel_rect: Optional[pygame.Rect] = None

    def set_tracks(self, tracks: list, current_idx: int = 0):
        self._tracks = tracks; self._current_idx = current_idx
        self._scroll = max(0, current_idx - 5)

    def set_current(self, idx: int):
        self._current_idx = idx
        # Auto-scroll to keep current track visible
        if self._panel_rect:
            visible = self._panel_rect.height // self.ROW_H
            if idx < self._scroll:                self._scroll = idx
            elif idx >= self._scroll + visible:   self._scroll = idx - visible + 1

    def toggle(self): self.open = not self.open

    def handle_mousedown(self, pos) -> bool:
        if not self.open or not self._panel_rect: return False
        if not self._panel_rect.collidepoint(pos): return False
        # Work out which row was clicked
        content_y = self._panel_rect.y + self.bar_h + 4
        rel_y = pos[1] - content_y
        if rel_y < 0: return True   # header area — consume but no action
        idx = self._scroll + rel_y // self.ROW_H
        if 0 <= idx < len(self._tracks):
            self.on_select(idx)
        return True

    def handle_mousemove(self, pos):
        if not self.open or not self._panel_rect: return
        content_y = self._panel_rect.y + self.bar_h + 4
        if self._panel_rect.collidepoint(pos):
            rel_y = pos[1] - content_y
            self._hover = self._scroll + rel_y // self.ROW_H if rel_y >= 0 else -1
        else:
            self._hover = -1

    def handle_mousewheel(self, pos, dy) -> bool:
        if not self.open or not self._panel_rect: return False
        if not self._panel_rect.collidepoint(pos): return False
        max_scroll = max(0, len(self._tracks) - self._panel_rect.height // self.ROW_H)
        self._scroll = max(0, min(max_scroll, self._scroll - dy * self.SCROLL_SPD))
        return True

    def update(self, dt: float):
        target = 1.0 if self.open else 0.0
        self._t = max(0.0, min(1.0, self._t + (target - self._t) * self.ANIM_SPEED * dt))

    def draw(self, surface, panel_alpha: float = 1.0):
        if self._t < 0.005: self._panel_rect = None; return
        anim = self._t; a_eff = anim * panel_alpha; ai = int(255 * a_eff)
        bh = self.bar_h

        # Panel dimensions: same left-side position as settings, full height
        pw = max(200, self.sw // 4)
        ph = self.sh - bh * 2 - 40
        x  = int(-pw - 20 + anim * (pw + 20)); x = min(20, x)
        y  = bh + 20
        self._panel_rect = pygame.Rect(x, y, pw, ph)

        draw_rounded_rect_alpha(surface, self._panel_rect, (10, 16, 26, int(255 * a_eff)),
            border_rgba=(*MEDIA_MID, int(255 * a_eff)), border_w=1, radius=self.RADIUS)

        # Header
        hdr_s = self.fonts["md_b"].render("Playlist", True, MEDIA_LITE)
        surface.blit(hdr_s, (x + 16, y + 12))
        count_s = self.fonts["sm"].render(f"{len(self._tracks)} tracks", True, (*TEXT_DIM, ai))
        surface.blit(count_s, (x + pw - count_s.get_width() - 12, y + 14))
        pygame.draw.line(surface, MEDIA_MID, (x + 10, y + bh - 2), (x + pw - 10, y + bh - 2), 1)

        # Track list (clipped)
        list_y = y + bh + 4
        list_h = ph - bh - 8
        if list_h <= 0 or not self._tracks: return
        try:
            clip = surface.subsurface(pygame.Rect(x + 1, list_y, pw - 2, list_h))
        except ValueError:
            return
        visible = list_h // self.ROW_H
        max_scroll = max(0, len(self._tracks) - visible)
        self._scroll = max(0, min(self._scroll, max_scroll))

        for i in range(visible + 1):
            idx = self._scroll + i
            if idx >= len(self._tracks): break
            ry = i * self.ROW_H
            is_cur = (idx == self._current_idx)
            is_hov = (idx == self._hover)
            if is_cur:
                draw_rounded_rect_alpha(clip, pygame.Rect(2, ry, pw - 6, self.ROW_H - 2),
                    (*MEDIA_MID, int(ai * 0.45)), radius=4)
            elif is_hov:
                draw_rounded_rect_alpha(clip, pygame.Rect(2, ry, pw - 6, self.ROW_H - 2),
                    (*MEDIA_DARK, int(ai * 0.6)), radius=4)
            # Playing indicator
            if is_cur:
                dot_s = self.fonts["sm"].render("▶", True, (*MEDIA_ACCENT, ai))
                clip.blit(dot_s, (6, ry + (self.ROW_H - dot_s.get_height()) // 2))
                txt_x = 20
            else:
                num_s = self.fonts["sm"].render(f"{idx+1}", True, (*TEXT_DIM, int(ai * 0.5)))
                clip.blit(num_s, (6, ry + (self.ROW_H - num_s.get_height()) // 2))
                txt_x = 20
            name = os.path.splitext(os.path.basename(self._tracks[idx]))[0]
            col  = TEXT_BOLD if is_cur else (TEXT_BRIGHT if is_hov else TEXT_MID_C)
            ts   = self.fonts["sm"].render(
                truncate_text(self.fonts["sm"], name, pw - txt_x - 14), True, (*col, ai))
            clip.blit(ts, (txt_x, ry + (self.ROW_H - ts.get_height()) // 2))

        # Scrollbar
        if len(self._tracks) > visible:
            sb_h = list_h - 4; tb_h = max(20, int(sb_h * visible / len(self._tracks)))
            tb_y = int((self._scroll / max(1, max_scroll)) * (sb_h - tb_h)) + 2
            pygame.draw.rect(clip, (*MEDIA_DARK, int(ai * 0.4)), (pw - 7, 2, 4, sb_h), border_radius=2)
            pygame.draw.rect(clip, (*MEDIA_MID, ai), (pw - 7, tb_y, 4, tb_h), border_radius=2)

# ── Main Application ───────────────────────────────────────────────────────────

class WavePlayer:
    TARGET_FPS = 30

    def __init__(self, wallpaper_path=None):
        pygame.init()
        pygame.display.set_caption(APP_NAME)
        self.screen=pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        self.W, self.H = self.screen.get_size()
        self._is_fullscreen = False
        self.clock=pygame.time.Clock(); self.running=True
        self._cmd_queue: list = []   # commands dispatched from STT thread, processed on main thread
        self._cmd_lock = threading.Lock()

        self._settings=load_settings()
        self.fonts=load_fonts(self._settings.get("ui_font",""))
        self._system_fonts=get_system_fonts()
        self.status=AppStatus()
        self.status.time_str=time.strftime("%H:%M")
        self.status.date_str=time.strftime("%A %d %B %Y").upper()

        # Wallpaper
        self._wallpaper_path=wallpaper_path or self._load_saved_wallpaper()
        self.wallpaper=self._load_wallpaper(self._wallpaper_path)

        # Core layout — all sizes relative to screen
        self._layout = _compute_layout(self.W, self.H)
        ly = self._layout
        _wave_r  = ly["circle_r"];  _wave_cy = ly["circle_cy"]
        _wave_cx = ly["circle_cx"]
        self.wave=WaveformCircle(_wave_cx, _wave_cy, _wave_r)

        # Media player
        self._media=MediaPlayerMode(_wave_cx, _wave_cy, _wave_r, self.fonts, layout=ly)
        self._media_stt_active=False

        # STT / TTS
        self._stt=VoskSTT(on_partial=self._stt_partial, on_final=self._stt_final, on_level=self._stt_level)
        self._stt._open_threshold=self._settings["mic_threshold"]
        self._tts=TTSEngine(on_level=self._tts_level_cb, on_start=self._tts_start_cb, on_end=self._tts_end_cb)
        self._tts.set_rate(int(self._settings.get("tts_rate",175)))
        self._tts.set_volume(float(self._settings.get("tts_volume",1.0)))
        self._tts_auto_muted=False

        # Wake-word gate
        self._wake_active   = False   # True after "computer" is heard
        self._wake_timer    = None    # threading.Timer that resets the gate
        self._wake_partial  = ""      # partial text being built after wake word

        # Dictation popup
        self.dictation=DictationPopup(self.W, self.H, self.fonts,
                                      on_submit=self._on_command_submit,
                                      circle_cy=_wave_cy, circle_r=_wave_r)

        # UI panels
        self._cmd_log=CommandLog()
        self.settings_panel=SettingsPanel(
            self.W, self.H, self.fonts, stt_ref=self._stt,
            settings=self._settings, on_settings_change=self._on_settings_change,
            font_list=self._system_fonts, on_font_select=self._on_font_select,
            command_log=self._cmd_log, bar_h=ly["bar_h"], panel_w=ly["panel_w"])

        # Log panel (F2) — command log on left side
        self.log_panel_open=False; self._log_panel_t=0.0

        self.help_panel=HelpPanel(self.W, self.H, self.fonts, bar_h=ly["bar_h"], panel_w=ly["panel_w"])
        self.wallpaper_browser=WallpaperBrowser(self.W, self.H, self.fonts,
                                                on_select=self._on_wallpaper_select,
                                                bar_h=ly["bar_h"])
        self.playlist_panel=PlaylistPanel(self.W, self.H, ly["bar_h"], self.fonts,
                                          on_select=self._on_playlist_select)

        self._vol_dragging = False
        self._seek_dragging = False
        self._last_save_t = 0.0   # for periodic playback state saves
        self._stt.start()
        self._tts.start()
        # Restore persisted mute state
        if bool(self._settings.get("mic_muted", 0.0)):
            self._stt.muted = True
            self.status.stt_muted = True
        threading.Thread(target=self._ipc_server, daemon=True).start()

    # ── TTS callbacks ──────────────────────────────────────────────────────────
    def _tts_level_cb(self, level): self.status.tts_level=level

    def _tts_start_cb(self):
        # Duck music while TTS speaks
        self._media.set_ducked(True)
        # Mute the STT mic so TTS audio isn't fed back into Vosk
        if not self._stt.muted:
            self._stt.muted = True
            self._tts_auto_muted = True
        else:
            self._tts_auto_muted = False
        # Cancel the wake-word timeout while TTS is speaking — we don't want
        # the gate to expire mid-sentence when TTS is answering a question
        if self._wake_timer is not None:
            self._wake_timer.cancel()
            self._wake_timer = None

    def _tts_end_cb(self):
        # Restore music volume after TTS finishes
        self._media.set_ducked(False)
        if self._tts_auto_muted:
            # Keep mic muted a bit longer to let the speaker ring die down,
            # then re-enable.  TTSEngine already sleeps _MIC_RELEASE_DELAY (1.2s)
            # before calling this, so this is just an extra safety margin.
            def _delayed_unmute():
                time.sleep(0.4)          # extra 0.4 s on top of TTSEngine's 1.2 s
                self._stt.muted = False
                self._tts_auto_muted = False
                # If we still have a pending artist question, re-arm the wake
                # gate so the user can answer without saying "computer" again.
                if self._media._pending_artist is not None:
                    self._wake_active = True
                    self._arm_wake_timer()
            threading.Thread(target=_delayed_unmute, daemon=True).start()
        else:
            # TTS was already muted by the user — don't touch mute state,
            # but still re-arm if there's a pending question.
            if self._media._pending_artist is not None:
                self._wake_active = True
                self._arm_wake_timer()

    # ── misc helpers ───────────────────────────────────────────────────────────
    def _log_cmd(self, text):
        self._cmd_log.add(text)
        self.settings_panel._log_autoscroll=True

    def _ack(self):
        self._tts.speak_immediate(random.choice(ACK_PHRASES))

    def _toggle_mute(self):
        self._stt.muted=not self._stt.muted; self.status.stt_muted=self._stt.muted
        self._log_cmd("mic muted" if self._stt.muted else "mic unmuted")
        # Persist mute state so it survives restarts
        self._settings["mic_muted"]=1.0 if self._stt.muted else 0.0
        save_settings(self._settings)
        if self._stt.muted and self.dictation.visible: self.dictation.dismiss()

    def _set_mute(self, muted):
        if self._stt.muted!=muted: self._toggle_mute()

    def _on_settings_change(self):
        self._stt._open_threshold=self._settings.get("mic_threshold",0.10)

    def _on_font_select(self, font_name):
        self._settings["ui_font"]=font_name; save_settings(self._settings)
        nf=load_fonts(font_name); self.fonts=nf
        self.dictation.fonts=nf; self.wallpaper_browser.fonts=nf
        self.settings_panel.refresh_fonts(nf); self.help_panel.refresh_fonts(nf)
        self._media.refresh_fonts(nf)

    def _on_wallpaper_select(self, path):
        self.wallpaper=self._load_wallpaper(path); self._wallpaper_path=path
        try:
            with open(WALLPAPER_FILE,"w") as f: f.write(path)
        except Exception as e: print(f"[Wallpaper] Save error: {e}")

    def _load_saved_wallpaper(self):
        if os.path.exists(WALLPAPER_FILE):
            try:
                p=open(WALLPAPER_FILE).read().strip()
                if os.path.isfile(p): return p
            except Exception: pass
        return None

    def _load_wallpaper(self, path):
        if path and os.path.exists(path):
            try:
                img=pygame.image.load(path).convert()
                return pygame.transform.scale(img,(self.W,self.H))
            except Exception as e: print(f"Wallpaper load error: {e}")
        return None

    def _toggle_fullscreen(self):
        """Toggle between fullscreen and windowed, updating layout in-place.
        Never touches the mixer — music plays continuously with no interruption."""
        # ── Resize display ───────────────────────────────────────────────────
        if self._is_fullscreen:
            self.screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
            self.W, self.H = self.screen.get_size()
            self._is_fullscreen = False
        else:
            pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.NOFRAME)
            info = pygame.display.Info()
            self.W, self.H = info.current_w, info.current_h
            self.screen = pygame.display.set_mode((self.W, self.H),
                                                   pygame.FULLSCREEN | pygame.NOFRAME)
            self._is_fullscreen = True
        # ── Recompute layout and update everything in-place ──────────────────
        self._layout = _compute_layout(self.W, self.H)
        ly = self._layout
        _wave_r=ly["circle_r"]; _wave_cy=ly["circle_cy"]; _wave_cx=ly["circle_cx"]
        # Waveform circle — recreate (no state)
        self.wave = WaveformCircle(_wave_cx, _wave_cy, _wave_r)
        # Media player — update geometry only, never recreate (would restart playback)
        self._media.update_layout(_wave_cx, _wave_cy, _wave_r, ly)
        # UI panels — update dimensions in place
        self.dictation.circle_cx=_wave_cx; self.dictation.circle_cy=_wave_cy
        self.dictation.circle_r=_wave_r; self.dictation.sw=self.W; self.dictation.sh=self.H
        self.wallpaper_browser.sw=self.W; self.wallpaper_browser.sh=self.H
        self.wallpaper_browser._bar_h=ly["bar_h"]
        tw=self.wallpaper_browser.COLS*(self.wallpaper_browser.THUMB_W+self.wallpaper_browser.THUMB_PAD)-self.wallpaper_browser.THUMB_PAD
        self.wallpaper_browser._grid_x=(self.W-tw)//2
        self.wallpaper_browser._grid_y=ly["bar_h"]+60
        self.settings_panel.w=self.W; self.settings_panel.h=self.H
        self.settings_panel._bar_h=ly["bar_h"]; self.settings_panel._panel_w=ly["panel_w"]
        self.help_panel.sw=self.W; self.help_panel.sh=self.H; self.help_panel._bar_h=ly["bar_h"]; self.help_panel._panel_w=ly["panel_w"]
        self.playlist_panel.sw=self.W; self.playlist_panel.sh=self.H; self.playlist_panel.bar_h=ly["bar_h"]
        if self._wallpaper_path:
            self.wallpaper=self._load_wallpaper(self._wallpaper_path)

    def _on_playlist_select(self, idx: int):
        """Called when user clicks a track in the playlist panel."""
        if 0 <= idx < len(self._media.tracks):
            self._media.stop()
            self._media.current_idx = idx
            self._media._resolve_art()
            self._media.play()
            self.playlist_panel.set_current(idx)

    def _sync_playlist(self):
        """Keep playlist panel in sync after scan or folder change."""
        self.playlist_panel.set_tracks(self._media.tracks, self._media.current_idx)

    def _current_panel_alpha(self): return self._settings.get("panel_alpha",0.82)
    def _current_bar_alpha(self):   return self._settings.get("bar_alpha",0.12)

    @property
    def _wake_word(self) -> str:
        """Live wake word from settings — empty string means no wake-word gate."""
        return self._settings.get("wake_word", "computer").strip().lower()

    # ── STT callbacks ──────────────────────────────────────────────────────────

    def _arm_wake_timer(self):
        """(Re)start the inactivity timer that drops the wake-word gate."""
        if self._wake_timer is not None:
            self._wake_timer.cancel()
        self._wake_timer = threading.Timer(WAKE_TIMEOUT, self._reset_wake_word)
        self._wake_timer.daemon = True
        self._wake_timer.start()

    def _reset_wake_word(self):
        """Drop back to sleeping — media ducking stops, popup dismissed."""
        self._wake_active  = False
        self._wake_partial = ""
        self._wake_timer   = None
        self._media_stt_active = False
        self._media.set_ducked(False)
        if self.dictation.visible:
            self.dictation.dismiss()

    def _stt_partial(self, raw_text, token_sets=None):
        raw_text = raw_text.strip()
        if not raw_text:
            return

        low = raw_text.lower()
        ww  = self._wake_word   # live from settings; empty = no gate

        # ── No-gate mode (wake word cleared in settings) ─────────────────────
        if not ww:
            self.dictation.update_partial(low)
            return

        # ── Wake-word detection in partial ───────────────────────────────────
        if not self._wake_active:
            if ww in low:
                self._wake_active  = True
                self._wake_partial = ""
                self._arm_wake_timer()
                after = low[low.index(ww) + len(ww):].strip()
                if after:
                    self._wake_partial = after
                    self.dictation.update_partial(after)
                else:
                    self.dictation.show("")   # show "Listening…" prompt
            # Not awake — ignore partial entirely (no popup, no ducking)
            return

        # ── Already awake — update display ───────────────────────────────────
        after = low
        if low.startswith(ww):
            after = low[len(ww):].strip()
        self._wake_partial = after
        self.dictation.update_partial(after if after else raw_text)
        self._arm_wake_timer()   # reset timeout on new speech

    def _stt_final(self, tokens, raw_text):
        low = raw_text.lower().strip()
        if not low:
            return

        # ── When mic is muted, only allow unmute commands through ─────────────
        if self._stt.muted:
            mc = VoskSTT.mute_command(low)
            if mc == "unmute":
                self._set_mute(False)
                self._tts.speak_immediate("listening")
            return

        ww = self._wake_word   # live from settings; empty = no gate

        # ── No-gate mode ──────────────────────────────────────────────────────
        if not ww:
            if self.dictation.visible:
                self.dictation.dismiss()
            with self._cmd_lock: self._cmd_queue.append(low)
            return

        # ── Check for wake word in final result ──────────────────────────────
        if ww in low:
            self._wake_active = True
            self._arm_wake_timer()
            after = low[low.index(ww) + len(ww):].strip()
            if self.dictation.visible:
                self.dictation.dismiss()
            if after:
                # Full "computer <command>" in one utterance — queue for main thread
                self._reset_wake_word()
                with self._cmd_lock: self._cmd_queue.append(after)
            else:
                # Just the wake word — stay armed, show listening prompt
                self.dictation.show("")
            return

        # ── Gate: ignore finals when wake word not active ────────────────────
        if not self._wake_active:
            if self.dictation.visible:
                self.dictation.dismiss()
            return

        # ── Wake word is active — dispatch command ────────────────────────────
        if self.dictation.visible:
            self.dictation.dismiss()
        self._reset_wake_word()
        with self._cmd_lock: self._cmd_queue.append(low)

    def _stt_level(self, level):
        self.status.stt_level = level
        self.status.stt_ready = self._stt.ready

        if self._stt.muted:
            self._media_stt_active = False
            self._media.set_ducked(False)
            return

        threshold = getattr(self._stt, "_open_threshold", 0.10)
        speech_detected = level > threshold

        # No-gate mode: duck whenever speech is detected (old behaviour)
        if not self._wake_word:
            if speech_detected:
                self._media_stt_active = True
                self._media.set_ducked(True)
            else:
                self._media_stt_active = False
                self._media.set_ducked(False)
            return

        # Wake-word gate mode: only duck after "computer" is heard
        if self._wake_active and speech_detected:
            self._media_stt_active = True
            self._media.set_ducked(True)
            self._arm_wake_timer()   # keep alive while speaking
        else:
            self._media_stt_active = False
            self._media.set_ducked(False)

    # ── command dispatcher ─────────────────────────────────────────────────────
    def _on_command_submit(self, text: str):
        low=text.lower().strip()
        if not low: return
        print(f"[CMD] '{low}'")

        # Help
        hc=VoskSTT.help_command(low)
        if hc=="open":  self.help_panel.set_open(True);  self._log_cmd("help opened"); self._ack(); return
        if hc=="close": self.help_panel.set_open(False); self._log_cmd("help closed"); self._ack(); return

        # Settings panel
        sc=VoskSTT.settings_command(low)
        if sc=="open":  self.settings_panel.set_open(True);  self._log_cmd("settings opened"); self._ack(); return
        if sc=="close": self.settings_panel.set_open(False); self._log_cmd("settings closed"); self._ack(); return

        # Playlist panel
        pc=VoskSTT.playlist_command(low)
        if pc=="open":
            if self.settings_panel.open: self.settings_panel.open=False
            self.playlist_panel.open=True;  self._log_cmd("playlist opened"); self._ack(); return
        if pc=="close": self.playlist_panel.open=False; self._log_cmd("playlist closed"); self._ack(); return

        # Wallpaper
        if VoskSTT.wallpaper_command(low):
            self.wallpaper_browser.open(); self._log_cmd("wallpaper browser"); self._ack(); return

        # Mute
        mc2=VoskSTT.mute_command(low)
        if mc2=="mute":   self._set_mute(True);  self._ack(); return
        if mc2=="unmute": self._set_mute(False); self._ack(); return

        # ── Media commands ────────────────────────────────────────────────────
        # Pending artist search reply — user is answering "which song?"
        # Strip wake word prefix if they said "computer <song name>" to answer.
        if self._media._pending_artist is not None:
            song_query = low
            ww = self._wake_word
            if ww and song_query.startswith(ww):
                song_query = song_query[len(ww):].strip()
            if not song_query:
                # Empty — leave pending state intact and re-arm so they can try again
                self._wake_active = True; self._arm_wake_timer(); return
            artist_matches=self._media._pending_artist_matches
            best_i,best_s=None,0.0
            q_words=self._media._word_set(song_query)
            for idx in artist_matches:
                fname=os.path.splitext(os.path.basename(self._media.tracks[idx]))[0]
                f_words=self._media._word_set(fname)
                score=self._media._fuzzy_word_score(q_words,f_words)
                if score>best_s: best_s,best_i=score,idx
            self._media._pending_artist=None; self._media._pending_artist_matches=[]
            if best_i is not None and best_s>=0.45:
                self._media.current_idx=best_i; self._media._resolve_art(); self._media.play()
                name=os.path.splitext(os.path.basename(self._media.tracks[best_i]))[0]
                self._tts.speak(f"Playing {name}"); self._log_cmd(f"play song: {name}")
            else:
                result=self._media.play_specific_track(song_query)
                self._tts.speak(result); self._log_cmd(result)
            return

        mc3=VoskSTT.media_command(low)

        if mc3 and mc3.startswith("play_artist_all:"):
            artist=mc3[len("play_artist_all:"):]
            matches=self._media.find_tracks_by_artist(artist)
            result=self._media.play_artist_shuffle(artist,matches=matches)
            self._tts.speak(result); self._log_cmd(result); return

        if mc3 and mc3.startswith("play_named:"):
            query=mc3[len("play_named:"):]
            result=self._media.find_best_match(query)
            rtype=result["type"]
            if rtype=="artist":
                matches=result["artist_matches"]
                if not matches: self._tts.speak(f"No tracks found for {query}"); return
                folder=os.path.basename(os.path.dirname(self._media.tracks[matches[0]]))
                if len(matches)==1:
                    msg=self._media.play_specific_track(query,track_idx=matches[0])
                    self._tts.speak(msg); self._log_cmd(msg)
                else:
                    self._media._pending_artist=query; self._media._pending_artist_matches=matches
                    self._tts.speak(f"Found {len(matches)} tracks by {folder}. Which song?")
                    self._log_cmd(f"artist match: {folder} ({len(matches)} tracks)")
            elif rtype=="song":
                msg=self._media.play_specific_track(query,track_idx=result["song_idx"])
                self._tts.speak(msg); self._log_cmd(f"play song: {msg}")
            else:
                # No confident match — try a looser artist shuffle as last resort
                loose_matches=self._media.find_tracks_by_artist(query)
                if loose_matches:
                    res=self._media.play_artist_shuffle(query,matches=loose_matches)
                    self._tts.speak(res); self._log_cmd(res)
                else:
                    self._tts.speak(f"Could not find {query}"); self._log_cmd(f"no match: {query}")
            return

        if mc3=="play":    self._media.play();                                   self._ack(); return
        if mc3=="pause":
            if self._media.playing and not self._media.paused: self._media.pause()
            elif self._media.paused: self._media.resume()
            self._ack(); return
        if mc3=="stop":    self._media.stop();                                   self._ack(); return
        if mc3=="next":    self._media.next_track();                             self._ack(); return
        if mc3=="prev":    self._media.prev_track();                             self._ack(); return
        if mc3=="shuffle": self._media.shuffle=not self._media.shuffle;          self._ack(); return
        if mc3=="repeat":  self._media.repeat=not self._media.repeat;            self._ack(); return
        if mc3=="vol_up":  self._media.set_volume(self._media.volume+0.15);      self._ack(); return
        if mc3=="vol_dn":  self._media.set_volume(self._media.volume-0.15);      self._ack(); return
        if mc3=="folder":  self._media.open_folder();                            self._ack(); return
        if mc3 and mc3.startswith("vol_set:"):
            try: self._media.set_volume(float(mc3.split(":")[1]))
            except Exception: pass
            self._ack(); return

        # Unrecognised
        self._log_cmd(f"? {low}")

    # ── IPC server ─────────────────────────────────────────────────────────────
    def _ipc_server(self):
        try:
            srv=_make_server_socket(); srv.listen(5); srv.settimeout(1.0)
        except Exception as e: print(f"[IPC] Server failed: {e}"); return
        while self.running:
            try:
                conn,_=srv.accept(); data=b""
                while True:
                    c=conn.recv(4096)
                    if not c: break
                    data+=c
                conn.close()
                for line in data.decode().splitlines():
                    line=line.strip()
                    if line: self._ipc(json.loads(line))
            except socket.timeout: pass
            except Exception: pass

    def _ipc(self, msg):
        t=msg.get("type","")
        if t=="media_play":    self._media.play()
        elif t=="media_pause":
            if self._media.playing and not self._media.paused: self._media.pause()
            elif self._media.paused: self._media.resume()
        elif t=="media_stop":  self._media.stop()
        elif t=="media_next":  self._media.next_track()
        elif t=="media_prev":  self._media.prev_track()
        elif t=="media_volume":
            v=msg.get("value",None)
            if v is not None: self._media.set_volume(float(v))
        elif t=="wallpaper":
            p=msg.get("path","")
            if p: self._on_wallpaper_select(p)
        elif t=="wallpaper_browser": self.wallpaper_browser.open()
        elif t=="mute":   self._set_mute(bool(msg.get("value",True)))
        elif t=="tts_speak":
            text=msg.get("text","")
            if text: self._tts.speak(text)
        elif t=="tts_speak_immediate":
            text=msg.get("text","")
            if text: self._tts.speak_immediate(text)
        elif t=="set_font":
            fn=msg.get("name","")
            if fn: self._on_font_select(fn)
        elif t=="settings":
            action=msg.get("action",None)
            self.settings_panel.set_open(action)

    # ── Main loop ──────────────────────────────────────────────────────────────
    def run(self):
        prev=time.time()
        while self.running:
            now=time.time(); dt=min(now-prev,0.05); prev=now
            self.status.time_str=time.strftime("%H:%M")
            self.status.date_str=time.strftime("%d %b %Y").upper()
            self.status.show_date=bool(self._settings.get("show_date",1.0))
            self.status.media_status=self._media.status_msg
            self.status.wake_active=self._wake_active

            # Update
            self.dictation.update(dt)
            self.wallpaper_browser.update(dt)
            self.help_panel.update(dt)
            self.settings_panel.update(dt)
            self.playlist_panel.update(dt)
            self._media.update(dt, self._media_stt_active)
            # Periodic position save (every 10 s) so position survives crashes too
            _now = time.time()
            if _now - self._last_save_t >= 10.0 and self._media.tracks:
                _cur = self._media.tracks[self._media.current_idx]
                _pos = (_now - self._media._start_t
                        if self._media.playing and not self._media.paused
                           and self._media._video_cap is None
                        else self._media._position)
                save_playback(track_path=_cur, position=max(0.0,_pos),
                              volume=self._media.volume,
                              shuffle=self._media.shuffle, repeat=self._media.repeat,
                              folder=self._media._current_folder)
                self._last_save_t = _now
            # Keep playlist in sync with current track
            if self._media.tracks and len(self._media.tracks) != len(self.playlist_panel._tracks):
                self.playlist_panel.set_tracks(self._media.tracks, self._media.current_idx)
            elif self._media.current_idx != self.playlist_panel._current_idx:
                self.playlist_panel.set_current(self._media.current_idx)
            self.wave.update(
                max(self.status.stt_level,
                    self._media.music_level if not self._media_stt_active else 0.0),
                self.status.tts_level, dt)

            # ── Drain STT command queue (safe: main thread only) ──────────────
            with self._cmd_lock:
                pending = list(self._cmd_queue)
                self._cmd_queue.clear()
            for _cmd in pending:
                self._on_command_submit(_cmd)

            # Events
            for ev in pygame.event.get():
                if ev.type==pygame.QUIT: self.running=False
                elif ev.type==pygame.VIDEORESIZE and not self._is_fullscreen:
                    self.W, self.H = ev.w, ev.h
                    self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
                    self._layout = _compute_layout(self.W, self.H)
                    ly = self._layout
                    _wave_r=ly["circle_r"]; _wave_cy=ly["circle_cy"]; _wave_cx=ly["circle_cx"]
                    self.wave = WaveformCircle(_wave_cx, _wave_cy, _wave_r)
                    self._media.cx=_wave_cx; self._media.cy=_wave_cy; self._media.r=_wave_r
                    self._media.CTRL_BTN_W=ly["btn_w"]; self._media.CTRL_BTN_H=ly["btn_h"]
                    self._media.VOL_BAR_H=ly["vol_bar_h"]
                    self._media._title_gap=ly["title_gap"]; self._media._time_gap=ly["time_gap"]
                    self._media._ctrl_gap=ly["ctrl_gap"]; self._media._bar_h=ly["bar_h"]
                    self._media._screen_h=ly["screen_h"]
                    self.dictation.sw=self.W; self.dictation.sh=self.H
                    self.dictation.circle_cx=_wave_cx; self.dictation.circle_cy=_wave_cy
                    self.dictation.circle_r=_wave_r
                    self.settings_panel.w=self.W; self.settings_panel.h=self.H
                    self.settings_panel._bar_h=ly["bar_h"]; self.settings_panel._panel_w=ly["panel_w"]
                    self.playlist_panel.sw=self.W; self.playlist_panel.sh=self.H
                    self.playlist_panel.bar_h=ly["bar_h"]
                    self.help_panel.sw=self.W; self.help_panel.sh=self.H
                    self.help_panel._bar_h=ly["bar_h"]; self.help_panel._panel_w=ly["panel_w"]
                    self.wallpaper_browser.sw=self.W; self.wallpaper_browser.sh=self.H
                    self.wallpaper_browser._bar_h=ly["bar_h"]
                    if self._wallpaper_path: self.wallpaper=self._load_wallpaper(self._wallpaper_path)
                elif ev.type==pygame.KEYDOWN:
                    k=ev.key; mods=pygame.key.get_mods(); ctrl=(mods&pygame.KMOD_CTRL)!=0
                    if self.settings_panel._font_dropdown and self.settings_panel._font_dropdown.open and k==pygame.K_ESCAPE:
                        self.settings_panel._font_dropdown.close(); continue
                    if self.settings_panel.handle_keydown(k, ev.unicode): continue
                    if self.wallpaper_browser.visible and self.wallpaper_browser.handle_key(k): continue
                    if self.help_panel.open and k==pygame.K_ESCAPE:
                        self.help_panel.set_open(False); continue
                    if self.dictation.handle_key(k): continue
                    if k==pygame.K_F1:
                        if self.playlist_panel.open: self.playlist_panel.open=False
                        self.settings_panel.toggle()
                    elif k==pygame.K_F2:
                        if self.settings_panel.open: self.settings_panel.open=False
                        self.playlist_panel.toggle()
                    elif ctrl and k==pygame.K_h: self.help_panel.toggle()
                    elif ctrl and k==pygame.K_m: self._toggle_mute()
                    elif ctrl and k==pygame.K_w: self.wallpaper_browser.open()
                    elif k==pygame.K_w and not ctrl:
                        if self._is_fullscreen: self._toggle_fullscreen()
                    elif k==pygame.K_f and not ctrl:
                        if not self._is_fullscreen: self._toggle_fullscreen()
                    elif k==pygame.K_EQUALS or k==pygame.K_KP_EQUALS:
                        if self.settings_panel.open: self.settings_panel.open=False
                        self.playlist_panel.toggle()
                    elif k==pygame.K_ESCAPE: self.running=False
                    else: self._media.handle_key(k, mods)

                elif ev.type==pygame.MOUSEBUTTONDOWN and ev.button==1:
                    if self.wallpaper_browser.visible: self.wallpaper_browser.handle_click(ev.pos); continue
                    if self.help_panel.handle_mousedown(ev.pos): continue
                    if self.settings_panel.handle_mousedown(ev.pos): continue
                    if self.playlist_panel.handle_mousedown(ev.pos): continue
                    # ── Settings button ──────────────────────────────────────
                    if _settings_btn_rect and _settings_btn_rect.collidepoint(ev.pos):
                        if self.playlist_panel.open: self.playlist_panel.open=False
                        self.settings_panel.toggle(); continue
                    # ── Fullscreen toggle button ─────────────────────────────
                    if _fullscreen_btn_rect and _fullscreen_btn_rect.collidepoint(ev.pos):
                        self._toggle_fullscreen(); continue
                    # ── Playlist button ──────────────────────────────────────
                    if _playlist_btn_rect and _playlist_btn_rect.collidepoint(ev.pos):
                        if self.settings_panel.open: self.settings_panel.open=False
                        self.playlist_panel.toggle(); continue
                    # ── Mute pill toggle ─────────────────────────────────────
                    if _mute_pill_rect and _mute_pill_rect.collidepoint(ev.pos):
                        self._toggle_mute(); continue
                    # ── CPU/RAM stats → toggle settings ─────────────────────
                    if _stats_rect and _stats_rect.collidepoint(ev.pos):
                        if self.playlist_panel.open: self.playlist_panel.open=False
                        self.settings_panel.toggle(); continue
                    # ── Volume bar drag start ─────────────────────────────────
                    vbr = self._media._vol_bar_rect
                    if vbr and vbr.collidepoint(ev.pos):
                        self._vol_dragging = True
                        rel = max(0.0, min(1.0, (ev.pos[0]-vbr.x)/max(1,vbr.width)))
                        self._media.set_volume(rel); continue
                    if self._media.handle_click(ev.pos):
                        # If the seek bar was clicked, start drag mode
                        sbr = self._media._seek_bar_rect
                        if sbr and sbr.collidepoint(ev.pos):
                            self._seek_dragging = True
                        continue

                elif ev.type==pygame.MOUSEBUTTONUP and ev.button==1:
                    self._vol_dragging  = False
                    self._seek_dragging = False
                    self.settings_panel.handle_mouseup(ev.pos)

                elif ev.type==pygame.MOUSEMOTION:
                    if self._vol_dragging:
                        vbr = self._media._vol_bar_rect
                        if vbr:
                            rel = max(0.0, min(1.0, (ev.pos[0]-vbr.x)/max(1,vbr.width)))
                            self._media.set_volume(rel)
                    if self._seek_dragging:
                        if self._media._seek_bar_rect and self._media._seek_bar_rect.collidepoint(ev.pos[0], self._media._seek_bar_rect.centery):
                            rel = max(0.0, min(1.0, (ev.pos[0] - self._media._seek_bar_x) / max(1, self._media._seek_bar_w)))
                            self._media.seek_to_frac(rel)
                        elif self._seek_dragging:
                            # Still dragging even if mouse drifts above/below bar
                            rel = max(0.0, min(1.0, (ev.pos[0] - self._media._seek_bar_x) / max(1, self._media._seek_bar_w)))
                            self._media.seek_to_frac(rel)
                    self.settings_panel.handle_mousemove(ev.pos)
                    self.playlist_panel.handle_mousemove(ev.pos)

                elif ev.type==pygame.MOUSEWHEEL:
                    mp=pygame.mouse.get_pos()
                    if self.help_panel.handle_mousewheel(mp,ev.y): pass
                    elif self.playlist_panel.handle_mousewheel(mp,ev.y): pass
                    elif not self.settings_panel.handle_mousewheel(mp,ev.y):
                        if self.wallpaper_browser.visible:
                            vr=self.wallpaper_browser._vrows()
                            rows=math.ceil(len(self.wallpaper_browser._images)/self.wallpaper_browser.COLS)
                            self.wallpaper_browser._scroll=max(0,min(rows-vr,self.wallpaper_browser._scroll-ev.y))

            # Draw
            if self.wallpaper: self.screen.blit(self.wallpaper,(0,0))
            else: self.screen.fill(DARK_BG)

            self._media.draw(
                self.screen, self.wave,
                self._media_stt_active,
                self.status.stt_level, self.status.tts_level, dt)
            if not self._media_stt_active:
                self.wave.draw(self.screen)

            pa=self._current_panel_alpha(); ba=self._current_bar_alpha()
            ly=self._layout
            draw_top_bar(self.screen,    self.fonts, self.status, self.W, self.H, bar_alpha=ba, bar_h=ly["bar_h"])
            draw_bottom_bar(self.screen, self.fonts, self.status, self.W, self.H, bar_alpha=ba, bar_h=ly["bar_h"])
            self.settings_panel.draw(self.screen, panel_alpha=pa)
            self.playlist_panel.draw(self.screen, panel_alpha=pa)
            self.help_panel.draw(self.screen, panel_alpha=pa)
            self.wallpaper_browser.draw(self.screen)
            self.dictation.draw(self.screen)

            pygame.display.flip()
            # Yield extra CPU when idle (no animation, no audio, no open panels)
            _busy = (self._media.playing or
                     self._tts.speaking or
                     self.status.stt_level > 0.01 or
                     self.settings_panel._t > 0.01 or
                     self.playlist_panel._t > 0.01 or
                     self.help_panel._t > 0.01 or
                     self.wallpaper_browser.visible or
                     self.dictation.visible)
            self.clock.tick(self.TARGET_FPS if _busy else 10)

        # Persist playback state so we can resume next time
        _save_track = self._media.tracks[self._media.current_idx] if self._media.tracks else ""
        # Compute live position: if playing, derive from _start_t for accuracy
        if self._media.playing and not self._media.paused and self._media._video_cap is None:
            _save_pos = time.time() - self._media._start_t
        else:
            _save_pos = self._media._position
        save_playback(
            track_path = _save_track,
            position   = max(0.0, _save_pos),
            volume     = self._media.volume,
            shuffle    = self._media.shuffle,
            repeat     = self._media.repeat,
            folder     = self._media._current_folder,
        )
        # Cleanup
        self._media.stop()
        self._stt.stop(); self._tts.stop()
        pygame.quit(); _cleanup_server_socket()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=f"{APP_NAME} v{VERSION}")
    ap.add_argument("--wallpaper", "-w", default=None)
    ap.add_argument("--windowed",  action="store_true")
    ap.add_argument("--font",      default=None)
    args = ap.parse_args()

    app = WavePlayer(wallpaper_path=args.wallpaper)
    if args.font: app._on_font_select(args.font)

    if args.windowed:
        app._is_fullscreen = False
        app.screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        app.W, app.H = app.screen.get_size()
        app.W, app.H = 1280, 720
        app._layout = _compute_layout(1280, 720)
        _ly = app._layout
        _wave_r  = _ly["circle_r"]; _wave_cy = _ly["circle_cy"]; _wave_cx = _ly["circle_cx"]
        app.wave  = WaveformCircle(_wave_cx, _wave_cy, _wave_r)
        app._media = MediaPlayerMode(_wave_cx, _wave_cy, _wave_r, app.fonts, layout=_ly)
        app.dictation = DictationPopup(1280, 720, app.fonts, on_submit=app._on_command_submit,
                                       circle_cy=_wave_cy, circle_r=_wave_r)
        app.wallpaper_browser = WallpaperBrowser(1280, 720, app.fonts,
                                                 on_select=app._on_wallpaper_select, bar_h=_ly["bar_h"])
        app.settings_panel = SettingsPanel(
            1280, 720, app.fonts, stt_ref=app._stt,
            settings=app._settings, on_settings_change=app._on_settings_change,
            font_list=app._system_fonts, on_font_select=app._on_font_select,
            command_log=app._cmd_log, bar_h=_ly["bar_h"], panel_w=_ly["panel_w"])
        app.help_panel = HelpPanel(1280, 720, app.fonts, bar_h=_ly["bar_h"], panel_w=_ly["panel_w"])

    print(f"\n{APP_NAME} v{VERSION}")
    print("-"*60)
    print("Wake word: 'computer'")
    print("  Say 'computer' then your command, or say it all in one go:")
    print("  'computer play next track'  /  'computer volume up'  etc.")
    print()
    print("F1 = toggle settings panel    CTRL+H = help")
    print("CTRL+M = toggle mic mute      CTRL+W = wallpaper browser")
    print("SPACE = play/pause   LEFT/RIGHT = prev/next   UP/DOWN = volume")
    print("S = shuffle   R = repeat   ESC = quit")
    print()
    print("Voice commands (after wake word):")
    print("  play / pause / stop / next / previous / shuffle / repeat")
    print("  volume up/down / set volume <0-100>")
    print("  play <song name>  /  play <artist name>")
    print("  play anything by <artist>")
    print("  open/change music folder")
    print("  show/open settings  /  close settings")
    print("  set wallpaper / browse wallpapers")
    print("  show help / close help")
    print("  stop listening / mute mic / start listening")
    print()
    print(f"IPC: {IPC_CFG['mode']} "
          + (f"path={IPC_CFG['socket_path']}" if IPC_CFG['mode']=='unix'
             else f"{IPC_CFG['host']}:{IPC_CFG['port']}"))
    print()

    app.run()