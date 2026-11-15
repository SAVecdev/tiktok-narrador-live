from pathlib import Path
from types import SimpleNamespace

import pytest

from narrador import local_voice
from narrador.local_voice import LocalVoice, QwenResourceError, _verified


def test_downloaded_models_match_pinned_hashes_and_synthesize_spanish():
    directory = Path(__file__).resolve().parent.parent / 'models'
    model = directory / 'kokoro-v1.0.int8.onnx'
    voices = directory / 'voices-v1.0.bin'
    if not model.exists() or not voices.exists():
        pytest.skip('Local Kokoro files are downloaded on first use.')
    import json
    manifest = json.loads((Path(__file__).resolve().parent.parent / 'narrador' / 'voice-models.json')
                          .read_text(encoding='utf-8'))
    assert _verified(model, manifest[model.name])
    assert _verified(voices, manifest[voices.name])
    wav = LocalVoice(directory).synthesize('Hola, bienvenida al directo.', 'ef_dora', 1.0)
    assert wav.startswith(b'RIFF') and wav[8:12] == b'WAVE'


def test_qwen_preflight_caches_resources_before_worker_load(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        float32='float32',
    )
    fake_psutil = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(available=13 * 1024 ** 3),
    )
    modules = (fake_torch, fake_psutil, object(), object, object(), type('Tqdm', (), {}))
    monkeypatch.setattr(local_voice, '_QWEN_MODULES', modules)
    monkeypatch.setattr(local_voice, '_QWEN_RUNTIME', None)

    prepared = local_voice.preload_qwen_runtime()

    assert prepared[-3:] == ('cpu', 'float32', 13.0)
    assert local_voice.preload_qwen_runtime(refresh_resources=False) is prepared


def test_qwen_preflight_rejects_low_memory_before_starting_worker(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        float32='float32',
    )
    fake_psutil = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(available=4 * 1024 ** 3),
    )
    modules = (fake_torch, fake_psutil, object(), object, object(), type('Tqdm', (), {}))
    monkeypatch.setattr(local_voice, '_QWEN_MODULES', modules)
    monkeypatch.setattr(local_voice, '_QWEN_RUNTIME', None)

    with pytest.raises(QwenResourceError):
        local_voice.preload_qwen_runtime()
