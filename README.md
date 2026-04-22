# WavePlayer — v1.0.0
A standalone voice-controlled media player,

made with PyGame-CE, Vosk for STT and pyttsx3 for TTS.

Features:
```
Audio (mp3/ogg/wav/flac/m4a/opus)
Video (mp4/avi/mkv/webm) via pygame + cv2
Album art / box-art rendered inside the circle
Animated equaliser bars
Vosk STT
TTS via espeak-ng (Linux) / SAPI5 (Windows) / pyttsx3 fallback
Wallpaper browser
Settings panel (transparency sliders, mic threshold, font picker)
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
stop listening / mute mic
start listening / unmute mic / listen to me
```

Key Commands:
```
<CTRL + H> Show Help Popup
<CTRL + W> Wallpaper Picker
<CTRL + M> Toggle STT Microphone Mute
ESC — quit
```
