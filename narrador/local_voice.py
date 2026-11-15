"""Pinned offline Kokoro voice runtime and verified model download."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import wave
from pathlib import Path

import httpx
from platformdirs import user_data_path

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
