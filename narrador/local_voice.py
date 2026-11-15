"""Offline speech engines: lightweight Kokoro and expressive Qwen3-TTS."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import httpx
from platformdirs import user_cache_path, user_data_path

from .diagnostics import LOGGER, record_exception

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / 'voice-models.json'
MODEL_FILES = ('kokoro-v1.0.int8.onnx', 'voices-v1.0.bin')


def model_dir() -> Path:
    local = ROOT / 'models'
    if all((local / name).is_file() for name in MODEL_FILES):
        return local
    return Path(user_data_path('TikTokNarradorLive')) / 'models'


def _verified(path: Path, details: dict) -> bool:
    if not path.is_file() or path.stat().st_size != details['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == details['sha256']


def ensure_models(progress=None, cancelled=None) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    target = model_dir()
    target.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        if cancelled and cancelled.is_set():
            raise InterruptedError('Voice download cancelled.')
        details = manifest[name]
        path = target / name
        if _verified(path, details):
            continue
        if path.exists():
            path.unlink()
        fd, temp_name = tempfile.mkstemp(prefix='.kokoro-', dir=target)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, 'wb') as output, httpx.stream(
                    'GET', details['url'], timeout=httpx.Timeout(10.0, connect=5.0),
                    follow_redirects=True) as response:
                response.raise_for_status()
                for block in response.iter_bytes(1024 * 1024):
                    if cancelled and cancelled.is_set():
                        raise InterruptedError('Voice download cancelled.')
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                    if progress:
                        progress(name, size, details['size'])
                output.flush()
                os.fsync(output.fileno())
            if size != details['size'] or digest.hexdigest() != details['sha256']:
                raise RuntimeError(f'La descarga de {name} no coincide con el archivo oficial verificado.')
            if cancelled and cancelled.is_set():
                raise InterruptedError('Voice download cancelled.')
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return target


class LocalVoice:
    def __init__(self, directory: Path | None = None, progress=None, cancelled=None):
        directory = directory or ensure_models(progress=progress, cancelled=cancelled)
        from kokoro_onnx import Kokoro
        self.engine = Kokoro(str(directory / MODEL_FILES[0]), str(directory / MODEL_FILES[1]))

    def synthesize(self, text: str, voice: str = 'ef_dora', speed: float = 1.0) -> bytes:
        import numpy as np

        samples, rate = self.engine.create(text[:1000], voice=voice, speed=max(.75, min(1.25, speed)), lang='es')
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise RuntimeError('El motor local no generó audio utilizable.')
        peak = float(np.max(np.abs(samples)))
        if peak > 0.98:
            samples = samples * (0.98 / peak)
        pcm = (samples * 32767).astype('<i2').tobytes()
        with tempfile.SpooledTemporaryFile() as buffer:
            with wave.open(buffer, 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(pcm)
            buffer.seek(0)
            return buffer.read()


QWEN_MODEL_ID = 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
QWEN_MODEL_REVISION = 'fa0251e3279a10b4936dc49d69a59c41b07cbfc0'
QWEN_ACCENTS = {
    'colombia_medellin': (
        'Acento de español colombiano de Medellín, Colombia, con un matiz paisa suave, natural y reconocible. '
        'No uses acento de España ni pronuncies como hablante de inglés.'
    ),
    'colombia_bogota': (
        'Acento de español colombiano de Bogotá, Colombia, claro y conversacional. '
        'No uses acento de España ni pronuncies como hablante de inglés.'
    ),
    'latino_neutral': (
        'Español latinoamericano neutral y natural, sin imitar el acento de España ni exagerar regionalismos.'
    ),
}


class QwenDependencyError(RuntimeError):
    """Actionable error shown when the optional Qwen runtime is missing."""


class QwenResourceError(RuntimeError):
    """Actionable error when the machine cannot safely load the 1.7B model."""


_QWEN_RUNTIME = None


def preload_qwen_runtime():
    """Load native Qwen/LLVM dependencies on the GUI thread before starting a worker."""
    global _QWEN_RUNTIME
    if _QWEN_RUNTIME is not None:
        return _QWEN_RUNTIME
    LOGGER.info('qwen.runtime.import.begin')
    try:
        import torch
        import psutil
        from qwen_tts import Qwen3TTSModel
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm
    except ImportError as exc:
        if getattr(sys, 'frozen', False):
            message = ('Qwen3-TTS no está incluido en este paquete portable. Usa la versión del código fuente '
                       'e instala requirements-qwen.txt; Kokoro local sí está disponible en el paquete.')
        else:
            message = ('Para usar Qwen3-TTS instala las dependencias locales con: '
                       'python -m pip install -r requirements-qwen.txt')
        raise QwenDependencyError(message) from exc
    _QWEN_RUNTIME = (torch, psutil, Qwen3TTSModel, snapshot_download, tqdm)
    LOGGER.info('qwen.runtime.import.ready')
    return _QWEN_RUNTIME


class QwenVoice:
    """Qwen3-TTS 1.7B voice design; weights are downloaded by Hugging Face on first use."""

    def __init__(self, progress=None, cancelled=None):
        torch, psutil, Qwen3TTSModel, snapshot_download, tqdm = preload_qwen_runtime()

        model_path = Path(user_cache_path('TikTokNarradorLive')) / 'qwen3-tts-1.7b-voicedesign'
        LOGGER.info('qwen.resources.check.begin')
        model_path.mkdir(parents=True, exist_ok=True)
        memory_gib = psutil.virtual_memory().available / (1024 ** 3)
        if torch.cuda.is_available():
            free_vram, _total_vram = torch.cuda.mem_get_info(0)
            if free_vram >= 7 * (1024 ** 3):
                device = 'cuda:0'
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            elif memory_gib >= 12:
                device = 'cpu'
                dtype = torch.float32
            else:
                raise QwenResourceError(
                    'Qwen 1.7B necesita unos 7 GB de VRAM libre o 12 GB de RAM disponible. '
                    'Libera memoria o selecciona Kokoro en Voz.'
                )
        elif getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
            if memory_gib < 12:
                raise QwenResourceError(
                    'Qwen 1.7B necesita unos 12 GB de memoria disponible. '
                    'Libera memoria o selecciona Kokoro en Voz.'
                )
            device = 'mps'
            dtype = torch.float16
        else:
            if memory_gib < 12:
                raise QwenResourceError(
                    'Qwen 1.7B necesita unos 12 GB de RAM disponible en este equipo. '
                    'Libera memoria o selecciona Kokoro en Voz.'
                )
            device = 'cpu'
            dtype = torch.float32

        class DownloadProgress(tqdm):
            def update(self, n=1):
                if cancelled and cancelled.is_set():
                    raise InterruptedError('Descarga de Qwen3-TTS cancelada.')
                result = super().update(n)
                if progress:
                    progress(self.desc or 'modelo Qwen', self.n, self.total or 0)
                return result

        if cancelled and cancelled.is_set():
            raise InterruptedError('Descarga de Qwen3-TTS cancelada.')
        LOGGER.info('qwen.resources.check.ready device=%s memory_available_gib=%.1f', device, memory_gib)
        LOGGER.info('qwen.model.download.begin revision=%s', QWEN_MODEL_REVISION)
        try:
            snapshot_download(
                repo_id=QWEN_MODEL_ID,
                revision=QWEN_MODEL_REVISION,
                local_dir=model_path,
                etag_timeout=5,
                max_workers=2,
                tqdm_class=DownloadProgress,
            )
        except InterruptedError:
            LOGGER.info('qwen.model.download.cancelled')
            raise
        except Exception as exc:
            record_exception('qwen model download failed', exc)
            raise
        if cancelled and cancelled.is_set():
            raise InterruptedError('Descarga de Qwen3-TTS cancelada.')

        LOGGER.info('qwen.model.load.begin device=%s memory_available_gib=%.1f', device, memory_gib)
        try:
            self.engine = Qwen3TTSModel.from_pretrained(
                str(model_path),
                local_files_only=True,
                device_map=device,
                dtype=dtype,
            )
        except Exception as exc:
            record_exception('qwen model load failed', exc)
            raise
        LOGGER.info('qwen.model.load.ready device=%s', device)

    @staticmethod
    def instruction(accent: str, style: str, pitch: float = 0.0, gender: str = 'FEMALE') -> str:
        regional = QWEN_ACCENTS.get(accent, QWEN_ACCENTS['colombia_medellin'])
        voice_gender = {
            'FEMALE': 'Voz femenina adulta joven,',
            'MALE': 'Voz masculina adulta joven,',
            'NEUTRAL': 'Voz adulta de género neutro,',
        }.get(gender, 'Voz femenina adulta joven,')
        if pitch >= 8:
            pitch_text = 'Registro vocal más agudo, sin forzarlo.'
        elif pitch >= 2:
            pitch_text = 'Registro vocal ligeramente agudo y luminoso.'
        elif pitch <= -8:
            pitch_text = 'Registro vocal más grave, sin forzarlo.'
        elif pitch <= -2:
            pitch_text = 'Registro vocal ligeramente más grave.'
        else:
            pitch_text = 'Registro vocal natural y medio.'
        return f'{regional} {voice_gender} {pitch_text} {style.strip()}'.strip()

    def synthesize(self, text: str, accent: str = 'colombia_medellin',
                   style: str = '', pitch: float = 0.0, gender: str = 'FEMALE') -> bytes:
        import numpy as np

        samples, rate = self.engine.generate_voice_design(
            text=text[:1000],
            language='Spanish',
            instruct=self.instruction(accent, style, pitch, gender),
        )
        samples = np.asarray(samples[0], dtype=np.float32)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise RuntimeError('Qwen3-TTS no generó audio utilizable.')
        peak = float(np.max(np.abs(samples)))
        if peak > 0.98:
            samples = samples * (0.98 / peak)
        pcm = (samples * 32767).astype('<i2').tobytes()
        with BytesIO() as buffer:
            with wave.open(buffer, 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(pcm)
            return buffer.getvalue()
