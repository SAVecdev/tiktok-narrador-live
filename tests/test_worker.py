"""Offline concurrency tests; no credentials, live stream or cloud calls."""
import asyncio
import time

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from narrador.config import Settings
from narrador.core import Event
from narrador.worker import SessionWorker

_APP = QCoreApplication.instance() or QCoreApplication([])


def prepared(mode="live", **settings):
    settings.setdefault('tts_engine', 'gemini')
    worker = SessionWorker(Settings(**settings), {}, 7, mode=mode)
    worker._queue = asyncio.Queue(maxsize=worker.QUEUE_SIZE)
    worker._dictations = asyncio.Queue(maxsize=3)
    worker._playback = asyncio.Event()
    return worker


class FakeServices:
    def __init__(self):
        self.calls = []

    async def synthesize(self, text):
        self.calls.append(text)
        return b"RIFF-test"


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def test_queue_is_bounded_and_identity_cooldown_applies():
    worker = prepared()
    for i in range(100):
        worker._enqueue(Event("comment", str(i), "mismo nombre", "hola"))
    assert worker._queue.qsize() == 20
    assert worker._overflow == 80
    other = prepared()
    other._enqueue(Event("comment", "same-id", "antes", "hola"))
    other._enqueue(Event("comment", "same-id", "después", "otra vez"))
    other._enqueue(Event("comment", "other-id", "antes", "hola"))
    assert other._queue.qsize() == 2


def test_consumer_waits_for_playback_before_next_cloud_call():
    async def scenario():
        worker = prepared()
        worker.MIN_INTERVAL = 0
        services = FakeServices()
        worker._enqueue(Event("comment", "1", "Ana", "primero"))
        worker._enqueue(Event("comment", "2", "Ana", "segundo"))
        task = asyncio.create_task(worker._consume(services))
        await settle()
        assert services.calls == ["primero"]
        worker._acknowledge()
        await settle()
        assert services.calls == ["primero", "segundo"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_budget_counts_failures_and_expired_events_do_not_call_cloud():
    from narrador.services import ProviderError

    class BrokenServices(FakeServices):
        async def synthesize(self, text):
            self.calls.append(text)
            raise ProviderError("Fallo controlado")

    async def scenario():
        worker = prepared()
        worker.CLOUD_BUDGET = 1
        worker.MIN_INTERVAL = 0
        services = BrokenServices()
        worker._queue.put_nowait(Event("comment", "old", "Ana", "antiguo", time.monotonic() - 31))
        worker._enqueue(Event("comment", "1", "Ana", "primero"))
        worker._enqueue(Event("comment", "2", "Ana", "segundo"))
        task = asyncio.create_task(worker._consume(services))
        await asyncio.wait_for(worker._queue.join(), 1)
        assert services.calls == ["primero"]
        assert worker._admitted == 1
        assert worker._budget_reported
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_demo_has_no_http_client_and_stop_completes_thread(monkeypatch):
    def no_cloud(*args, **kwargs):
        raise AssertionError("Demo must not construct HTTP clients")
    monkeypatch.setattr("narrador.worker.httpx.AsyncClient", no_cloud)
    async def no_greeting(self, services):
        return None
    monkeypatch.setattr(SessionWorker, '_initial_greeting', no_greeting)
    worker = SessionWorker(Settings(tts_engine='local'), {}, 3, mode="demo")
    worker.start()
    deadline = time.monotonic() + 2
    while worker._queue is None and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert worker._queue is not None
    worker.inject("comment", "1", "Ana", "hola")
    worker.stop()
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(2500, loop.quit)
    if worker.isRunning():
        loop.exec()
    assert not worker.isRunning()


def test_initial_demo_greeting_uses_the_selected_ai_voice(monkeypatch):
    worker = prepared(mode='demo', tts_engine='qwen',
                      initial_greeting='Hola, ya estamos en directo.')

    async def fake_synthesize(text):
        assert text == 'Hola, ya estamos en directo.'
        return b'RIFF-ai-voice'

    played = []
    async def fake_play(text, data):
        played.append((text, data))
        return True

    monkeypatch.setattr(worker, '_synthesize_local', fake_synthesize)
    monkeypatch.setattr(worker, '_play', fake_play)
    asyncio.run(worker._initial_greeting(None))
    assert played == [('Hola, ya estamos en directo.', b'RIFF-ai-voice')]


def test_demo_voice_error_is_logged_without_ending_consumer(monkeypatch):
    worker = prepared(mode='demo', tts_engine='local')
    worker._enqueue(Event('comment', '1', 'Ana', 'hola'))

    async def fail_synthesis(_text):
        raise RuntimeError('synthetic engine failure')

    monkeypatch.setattr(worker, '_synthesize_local', fail_synthesis)

    async def scenario():
        task = asyncio.create_task(worker._consume(None))
        await asyncio.wait_for(worker._queue.join(), 1)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_stop_before_start_is_safe():
    worker = SessionWorker(Settings(), {"tts": "never-used"}, 4, mode="demo")
    worker.stop()
    worker.start()
    assert worker.wait(2000)
    assert worker.keys == {}


def test_playback_timeout_stops_consumer_instead_of_overlapping():
    async def scenario():
        worker = prepared()
        worker.PLAYBACK_TIMEOUT = 0.01
        worker.MIN_INTERVAL = 0
        services = FakeServices()
        worker._enqueue(Event("comment", "1", "Ana", "primero"))
        worker._enqueue(Event("comment", "2", "Ana", "segundo"))
        try:
            await worker._consume(services)
        except TimeoutError:
            pass
        else:
            raise AssertionError("Expected playback timeout")
        assert services.calls == ["primero"]
    asyncio.run(scenario())


def test_answer_failure_does_not_repeat_original_comment_as_speech():
    from narrador.services import ProviderError

    class BrokenReply:
        def __init__(self):
            self.spoken = []

        async def reply(self, name, text):
            raise ProviderError('Ollama no respondió correctamente.')

        async def synthesize(self, text):
            self.spoken.append(text)
            return b'RIFF-test'

    async def scenario():
        worker = SessionWorker(Settings(mode='answer', tts_engine='gemini'), {}, 5, mode='live')
        worker._queue = asyncio.Queue(maxsize=worker.QUEUE_SIZE)
        worker._playback = asyncio.Event()
        worker.MIN_INTERVAL = 0
        service = BrokenReply()
        worker._enqueue(Event('comment', '1', 'Ana', 'hola'))
        task = asyncio.create_task(worker._consume(service))
        await asyncio.wait_for(worker._queue.join(), 1)
        assert service.spoken == []
        assert worker._admitted == 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_reply_returned_after_comment_expiry_is_not_synthesized():
    class SlowServices:
        def __init__(self):
            self.synthesized = []
        async def reply(self, name, text):
            await asyncio.sleep(0.02)
            return 'respuesta tardía'
        async def synthesize(self, text):
            self.synthesized.append(text)
            return b'RIFF-test'

    async def scenario():
        worker = SessionWorker(Settings(mode='answer', tts_engine='gemini'), {}, 9, mode='live')
        worker._queue = asyncio.Queue(maxsize=worker.QUEUE_SIZE)
        worker._playback = asyncio.Event()
        worker.MAX_AGE = 0.01
        worker.MIN_INTERVAL = 0
        service = SlowServices()
        worker._enqueue(Event('comment', '1', 'Ana', 'hola'))
        task = asyncio.create_task(worker._consume(service))
        await asyncio.wait_for(worker._queue.join(), 1)
        assert service.synthesized == []
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_slow_tts_does_not_speak_stale_comment():
    class SlowTTS:
        def __init__(self):
            self.played = []
        async def synthesize(self, text):
            await asyncio.sleep(0.02)
            return b'RIFF-test'

    async def scenario():
        worker = SessionWorker(Settings(mode='read', tts_engine='gemini'), {}, 10, mode='live')
        worker._queue = asyncio.Queue(maxsize=worker.QUEUE_SIZE)
        worker._playback = asyncio.Event()
        worker.MAX_AGE = 0.01
        worker.MIN_INTERVAL = 0
        spoken = []
        worker.spoken.connect(lambda _sid, text: spoken.append(text))
        worker._enqueue(Event('comment', '1', 'Ana', 'hola'))
        task = asyncio.create_task(worker._consume(SlowTTS()))
        await asyncio.wait_for(worker._queue.join(), 1)
        assert spoken == []
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_late_join_is_persistent_priority_and_marked_after_audio(tmp_path):
    from narrador.memory import MemoryStore

    async def scenario():
        worker = prepared()
        worker.settings.greeting_template = 'Hola {usuario}, bienvenida.'
        worker._memory = MemoryStore(tmp_path / 'joins.sqlite3')
        worker._room_key = 'room-1'
        worker._enqueue(Event('join', 'viewer-1', 'Ana', created=time.monotonic() - 300))
        worker._enqueue(Event('comment', 'viewer-2', 'Luis', 'mensaje'))
        spoken = []
        worker.spoken.connect(lambda _sid, text: spoken.append(text))
        service = FakeServices()
        task = asyncio.create_task(worker._consume(service))
        await settle()
        assert spoken == ['Hola Ana, bienvenida.']
        assert worker._memory.pending_joins('room-1') == [('viewer-1', 'Ana')]
        worker._acknowledge()
        await settle()
        assert worker._memory.pending_joins('room-1') == []
        assert service.calls == ['Hola Ana, bienvenida.']
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
