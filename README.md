# TikTok Narrador LIVE

Aplicación de escritorio gratuita y de código abierto que escucha comentarios y nuevas entradas en TikTok LIVE, y las lee o responde en voz alta. Se configura desde la interfaz, sin editar archivos `.env`.

**Estado:** versión inicial de pruebas. La demo local está disponible. La conexión real depende de cambios en TikTok, del servicio de firma y de credenciales personales de Google. Todavía hacen falta compilaciones y pruebas de aceptación en Windows y macOS.

## Probar en desarrollo

Requiere Python 3.12 o posterior y una sesión gráfica con audio. En Mac Intel, usa Python 3.12 o 3.13 (ONNX Runtime aún no publica rueda Intel para Python 3.14; Qwen/PyTorch tampoco ofrece el runtime opcional para Mac Intel). En Linux, el micrófono requiere PortAudio del sistema (por ejemplo, `libportaudio2` en Ubuntu/Debian). Para Qwen3-TTS instala también `requirements-qwen.txt`; este instala PyTorch de CPU y evita las bibliotecas CUDA en equipos sin GPU NVIDIA. Una GPU compatible puede acelerar la generación; en CPU puede tardar más.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -r requirements-qwen.txt
python run.py
```

### GPU AMD en Linux

Para usar Qwen3-TTS con una Radeon compatible con ROCm, crea un entorno independiente con Python 3.12. El entorno normal de CPU y los entornos para Windows/macOS no se modifican:

```bash
python3.12 -m venv .venv-amd
.venv-amd/bin/python -m pip install -r requirements.txt -r requirements-amd-rocm.txt
bash run_amd.sh
```

PyTorch usa la API `torch.cuda` también con ROCm. El controlador AMD del sistema debe exponer `/dev/kfd` y `/dev/dri/renderD*`, y la cuenta debe tener acceso a ellos. Las ruedas ROCm de AMD incluyen soporte para `gfx1200`; consulta la [instalación oficial de PyTorch ROCm](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/frameworks/pytorch/install.html) para otros modelos de GPU y versiones de Linux.

Para respuestas locales instala Ollama y descarga el modelo sugerido para una GPU de 16 GB (Qwen3.5 4B):

```bash
ollama pull qwen3.5:4b
```

Qwen3.5 9B también está disponible si se prioriza calidad y hay VRAM libre, pero 4B deja más espacio para mantener la síntesis Qwen-TTS cargada a la vez. El modo local de la app desactiva el razonamiento extendido y limita las respuestas para reducir la latencia en directo. En Linux con GPU AMD, PyTorch y Ollama deben tener sus runtimes ROCm instalados y ver la GPU; instalar solo los pesos no activa la aceleración.

Pulsa **Demo** y simula un comentario o una llegada para probar la interfaz. Qwen3-TTS 1.7B VoiceDesign es la voz local predeterminada. Al generar el primer audio, Qwen descarga sus pesos (el modelo ocupa unos 4,52 GB) y los mantiene en la caché local de Hugging Face; el código fija una revisión del modelo. La demo no requiere clave de API. En Linux o Windows sin GPU compatible, la primera síntesis puede tardar bastante. Kokoro sigue disponible como alternativa ligera: descarga el modelo int8 (unos 88 MiB) y las voces (unos 27 MiB), verifica sus SHA-256 y funciona sin GPU. Activa el micrófono desde **Voz** para transcribir localmente y repetir lo que dices con la voz sintética seleccionada; Whisper Tiny descarga unos 75 MiB la primera vez.

## Preparar servicios externos

Las claves se escriben en la pestaña **API y privacidad**. **Guardar** recuerda las preferencias normales; las claves solo persisten si se activa el almacén seguro del sistema. La app no escribe claves en `.env`, configuración JSON ni registros. Si el sistema no ofrece un almacén seguro compatible, la clave permanece en la sesión actual.

1. Crea una clave de Gemini API en Google AI Studio. Se usa para generar voz Gemini TTS; revisa el acceso, cuotas y precios del proyecto.
2. La clave de AI Studio también sirve si eliges respuestas con Gemini. Ollama es el modo local predeterminado. Puedes elegir el modo «Leer comentarios», «Responder con IA» o «Ignorar comentarios». En «¿Qué estás haciendo en el directo?» describe el juego o actividad para dar contexto a las respuestas. Ollama usa ese contexto sin enviarlo a Google.
3. Escribe el nombre de usuario del creador sin `@` y usa **Iniciar LIVE** cuando esté transmitiendo. Euler Stream es un servicio externo opcional para firmar la conexión de TikTok; la disponibilidad pública sin clave puede variar. No se promete acceso gratuito o disponibilidad permanente.

Si TikTok o la red corta el WebSocket de LIVE, la sesión permanece abierta y la app reintenta la conexión automáticamente con esperas progresivas. El estado y los reintentos aparecen en **Actividad de la sesión**; pulsa **Detener** para cancelar los reintentos.

La app solo habla las respuestas en el audio del ordenador. No publica comentarios en TikTok. Puedes capturar el audio de escritorio en OBS. Ollama local recibe el comentario y la actividad indicada. Gemini TTS recibe el texto convertido en voz; si eliges Gemini para responder, también recibe el comentario, nombre y actividad del directo. Las claves se envían solo al proveedor al que pertenecen. Las palabras bloqueadas se filtran antes de enviar contenido.

## Configuración de voz

En **Voz** elige Qwen3-TTS local, Kokoro local o Gemini TTS. Qwen VoiceDesign genera una voz a partir de instrucciones: el perfil inicial es mujer adulta joven, expresiva y ligeramente coqueta, con opciones de acento de Medellín, Bogotá y español latino neutral. Es una descripción para el generador, no una grabación ni una voz colombiana certificada; el modelo puede aproximarse de forma distinta en cada frase. El control de tonalidad se traduce a una instrucción de voz. El modelo oficial admite español y diseño de voces por texto, pero sus voces `CustomVoice` pregrabadas no incluyen una voz colombiana. [Detalles y voces compatibles de Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS).

Al iniciar **Demo** o conectarse al LIVE, la app pronuncia el campo **Saludo inicial · voz IA** antes de leer comentarios. Cuando alguien se une, Ollama o Gemini redacta un saludo personalizado con su nombre y la actividad del directo; en Demo se usa solo Ollama local. El estilo se ajusta en **Comportamiento**. Las respuestas locales son más breves para reducir el tiempo de generación y Ollama mantiene el modelo cargado durante la sesión. Qwen sintetiza el audio completo antes de reproducirlo: en CPU puede tardar más; Kokoro es la alternativa ligera si necesitas menor latencia.

Qwen3-TTS 1.7B está bajo Apache-2.0 y sus pesos se descargan de Hugging Face bajo demanda; no se suben al repositorio ni se incluyen en los paquetes portables. Para responder en LIVE sin retrasos conviene probar primero **Probar voz** en el equipo que se usará. Kokoro es la alternativa ligera si el hardware no alcanza. El micrófono se activa de forma explícita, Whisper transcribe en el equipo y el audio no se guarda ni se sube. Los dictados esperan su turno sin interrumpir lo que ya está hablando.

Los paquetes portables actuales todavía no incluyen PyTorch ni el runtime `qwen-tts`; para usar Qwen instálalo desde el código fuente siguiendo los pasos anteriores. En el paquete portable puedes seleccionar Kokoro o Gemini.

Para diagnosticar fallos, abre **API y privacidad → Abrir carpeta de registros**. `app.log` y sus rotaciones guardan pasos técnicos y trazas sin mensajes de comentarios, claves ni audio.

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
