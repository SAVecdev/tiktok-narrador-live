import pytest

from narrador.microphone import MicWorker


def test_whisper_download_uses_fixed_revision_and_cancelable_progress(monkeypatch, tmp_path):
    observed = {}

    def snapshot_download(repo, **kwargs):
        observed['repo'] = repo
        observed.update(kwargs)
        return str(tmp_path)

    class FakeWhisper:
        def __init__(self, path, **kwargs):
            observed['path'] = path
            observed['model_kwargs'] = kwargs

    monkeypatch.setattr('huggingface_hub.snapshot_download', snapshot_download)
    worker = MicWorker()
    worker._load_model(FakeWhisper)
    assert observed['repo'] == 'Systran/faster-whisper-tiny'
    assert observed['revision'] == 'd90ca5fe260221311c53c58e660288d3deb8d356'
    assert observed['max_workers'] == 1
    assert observed['model_kwargs'] == {'device': 'cpu', 'compute_type': 'int8'}


def test_whisper_download_stops_when_requested(monkeypatch):
    worker = MicWorker()
    worker.stop()
    monkeypatch.setattr('huggingface_hub.snapshot_download',
                        lambda *_args, **_kwargs: pytest.fail('download must not start'))
    with pytest.raises(InterruptedError):
        worker._load_model(object)


def test_whisper_download_progress_callback_cancels_transfer(monkeypatch):
    worker = MicWorker()

    def snapshot_download(_repo, **kwargs):
        bar = kwargs['tqdm_class'](total=100)
        worker.stop()
        bar.update(1)

    monkeypatch.setattr('huggingface_hub.snapshot_download', snapshot_download)
    with pytest.raises(InterruptedError):
        worker._load_model(object)
