from pathlib import Path

import pytest

from narrador.local_voice import LocalVoice, _verified


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
