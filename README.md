# WavePlayer — v1.0.0
A standalone voice-controlled media player with Vosk STT, TTS, and
an animated waveform circle.

Features:
```
  • Audio (mp3/ogg/wav/flac/m4a/opus) and Video (mp4/avi/mkv/webm) via pygame + cv2
  • Album art / box-art rendered inside the circle
  • Animated equaliser bars
  • Full transport controls arc-rendered below the circle
  • Vosk STT — speech detection ducks media volume and shows popup
  • TTS via espeak-ng (Linux) / SAPI5 (Windows) / pyttsx3 fallback
  • Wallpaper browser
  • Settings panel (transparency sliders, mic threshold, font picker)
  • IPC (Unix socket / TCP) for external control
```

Voice commands:
```
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
```