# TikTok Narrador LIVE

Aplicación de escritorio gratuita y de código abierto que escucha comentarios y nuevas entradas en TikTok LIVE, y las lee o responde en voz alta. Se configura desde la interfaz, sin editar archivos `.env`.

**Estado:** versión inicial de pruebas. La demo local está disponible. La conexión real depende de cambios en TikTok, del servicio de firma y de credenciales personales de Google. Todavía hacen falta compilaciones y pruebas de aceptación en Windows y macOS.

## Probar en desarrollo

Requiere Python 3.12 o posterior y una sesión gráfica con audio. En Mac Intel, usa Python 3.12 o 3.13 (ONNX Runtime aún no publica rueda Intel para Python 3.14). En Linux, el micrófono requiere PortAudio del sistema (por ejemplo, `libportaudio2` en Ubuntu/Debian); Kokoro TTS funciona sin dispositivo de entrada.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python run.py
```

Pulsa **Demo** y simula un comentario o una llegada para probar la interfaz. Kokoro local es la voz predeterminada; la primera prueba descarga el modelo int8 (unos 88 MiB) y las voces (unos 27 MiB) y verifica sus SHA-256. La demo no requiere clave de API. Activa el micrófono desde **Voz** para transcribir localmente y repetir lo que dices con Dora; la primera activación descarga el modelo Whisper Tiny (unos 75 MiB).

## Preparar servicios externos

Las claves se escriben en la pestaña **API y privacidad**. **Guardar** recuerda las preferencias normales; las claves solo persisten si se activa el almacén seguro del sistema. La app no escribe claves en `.env`, configuración JSON ni registros. Si el sistema no ofrece un almacén seguro compatible, la clave permanece en la sesión actual.

1. Crea una clave de Gemini API en Google AI Studio. Se usa para generar voz Gemini TTS; revisa el acceso, cuotas y precios del proyecto.
2. La clave de AI Studio también sirve si eliges respuestas con Gemini. Ollama es el modo local predeterminado. Puedes elegir el modo «Leer comentarios», «Responder con IA» o «Ignorar comentarios». En «¿Qué estás haciendo en el directo?» describe el juego o actividad para dar contexto a las respuestas. Ollama usa ese contexto sin enviarlo a Google.
3. Escribe el nombre de usuario del creador sin `@` y usa **Iniciar LIVE** cuando esté transmitiendo. Euler Stream es un servicio externo opcional para firmar la conexión de TikTok; la disponibilidad pública sin clave puede variar. No se promete acceso gratuito o disponibilidad permanente.

La app solo habla las respuestas en el audio del ordenador. No publica comentarios en TikTok. Puedes capturar el audio de escritorio en OBS. Ollama local recibe el comentario y la actividad indicada. Gemini TTS recibe el texto convertido en voz; si eliges Gemini para responder, también recibe el comentario, nombre y actividad del directo. Las claves se envían solo al proveedor al que pertenecen. Las palabras bloqueadas se filtran antes de enviar contenido.

## Configuración de voz

En **Voz** elige Kokoro local o Gemini TTS. La voz local femenina en español `ef_dora` funciona sin API y se descarga una vez; el modelo publica una voz femenina en español, pero no garantiza un acento latino regional. El micrófono se activa de forma explícita, Whisper transcribe en el equipo y nunca se guarda ni se sube audio. Los dictados se sintetizan con la voz IA seleccionada y esperan su turno sin interrumpir lo que ya está hablando. El control de tono avanzado y las voces de otros géneros están disponibles con Gemini; Kokoro permite elegir la voz y ajustar la velocidad entre 0,75 y 1,25.

En **Comportamiento**, escribe la actividad y los datos que deben servir de contexto. Si marcas **Guardar contexto**, se conserva una copia local en SQLite para futuros directos; al desmarcarla y guardar, se borra ese contexto persistente. Puedes borrar la memoria desde la misma pestaña. Gemini solo recibe esa actividad y memoria si marcas además su casilla de permiso.

## Descargar

Los archivos portables se publican en [GitHub Releases](https://github.com/SAVecdev/tiktok-narrador-live/releases) cuando se crea una etiqueta `v*`. Cada archivo corresponde a un sistema operativo y arquitectura. En macOS y Windows, una app sin firma puede mostrar una advertencia al iniciar. En Linux se requiere un escritorio y bibliotecas multimedia del sistema.

## Desarrollo y licencia

```bash
python -m pytest -q
python -m ruff check .
python -m compileall -q narrador
python run.py --smoke /tmp/narrador-smoke.png
```

La compilación de PyInstaller se ejecuta en el sistema operativo objetivo: `python -m PyInstaller --noconfirm Narrador.spec`. Consulta [TERCEROS](THIRD_PARTY_NOTICES.md) y [LICENSE](LICENSE). TikTokLive usa AGPL-3.0; este proyecto se publica bajo AGPL-3.0-or-later.

No está afiliada a TikTok, Google ni Euler Stream. TikTokLive es una integración comunitaria no oficial y puede dejar de funcionar cuando TikTok cambia su protocolo.
