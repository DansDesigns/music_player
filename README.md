# WavePlayer — v1.0.0
A standalone voice-controlled media player,

* recomended to have wallpapers in a ```wallpapers``` folder in ```/Home/Pictures/``` on Linux, or in ```User\Pictures\``` on Windows 
* use "Mic Threashold" on settings panel to change to trigger level for STT
* set a custom wakeword in the Settings Panel


Features:
```
Made with PyGame-CE 
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
<CTRL + H> Show Help Panel
<CTRL + W> Wallpaper Picker
<CTRL + M> Toggle STT Microphone Mute
<F1> Toggle Settings Panel
ESC — quit
```
