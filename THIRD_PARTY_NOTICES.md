# Componentes de terceros

TikTok Narrador Live se distribuye bajo AGPL-3.0-or-later. El código correspondiente
se publica en https://github.com/SAVecdev/tiktok-narrador-live en el mismo commit/tag
que cada distribución. Las modificaciones redistribuidas deben cumplir la licencia.

- TikTokLive: AGPL-3.0, https://github.com/isaackogan/TikTokLive
- PySide6 / Qt: LGPL-3.0/GPL-3.0 o licencia comercial, https://www.qt.io/licensing/
  Se enlaza dinámicamente; las bibliotecas incluidas no se modifican. Los avisos y
  textos de licencia acompañan a los metadatos del paquete y a Qt en la distribución.
- httpx: BSD-3-Clause, https://github.com/encode/httpx
- keyring: MIT, https://github.com/jaraco/keyring
- platformdirs: MIT, https://github.com/tox-dev/platformdirs
- Kokoro-ONNX runtime: MIT, https://github.com/thewh1teagle/kokoro-onnx
- Kokoro-82M model and voice vectors (`ef_dora`): Apache-2.0,
  https://huggingface.co/hexgrad/Kokoro-82M
- Qwen3-TTS Python package: Apache-2.0, https://github.com/QwenLM/Qwen3-TTS
- Qwen3-TTS 12Hz 1.7B VoiceDesign model: Apache-2.0,
  https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
- eSpeak-NG phonemizer runtime: GPL-3.0, https://github.com/espeak-ng/espeak-ng
- faster-whisper transcription runtime: MIT, https://github.com/SYSTRAN/faster-whisper
- faster-whisper Tiny checkpoint: MIT, https://huggingface.co/Systran/faster-whisper-tiny
- ONNX Runtime: MIT, https://github.com/microsoft/onnxruntime
- sounddevice: MIT, https://github.com/spatialaudio/python-sounddevice
- PyInstaller (herramienta de empaquetado): GPL-2.0 con excepción de distribución,
  https://pyinstaller.org/en/stable/license.html

Los metadatos de las dependencias y sus licencias se incluyen en `_internal`.
No se incluye ninguna clave, cuenta ni crédito de servicios externos.
Google, TikTok y Euler Stream son servicios independientes con sus propios términos.
El proyecto no está afiliado a esas empresas.

El método de diseño EGM procede de google/elephant-goldfish-model (Apache-2.0).
Los documentos de este proyecto registran decisiones y revisiones propias; no
se redistribuye el plugin EGM como parte de la aplicación.
