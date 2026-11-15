"""A single cancellable asyncio session; all playback remains on the GUI thread."""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time

import httpx
from PySide6.QtCore import QThread, Signal

from .core import Event, Policy, clean_text
from .diagnostics import LOGGER, record_exception
from .local_voice import LocalVoice, QwenDependencyError, QwenResourceError, QwenVoice
from .memory import MemoryStore
from .services import GoogleServices, ProviderError


class SessionWorker(QThread):
    status = Signal(int, str)
    log = Signal(int, str)
    spoken = Signal(int, str)
    audio = Signal(int, bytes)
    demo_speech = Signal(int, str)
    catalogue = Signal(int, str, object)

    QUEUE_SIZE = 20
    MAX_AGE = 30.0
    CONNECT_TIMEOUT = 30.0
    API_TIMEOUT = 30.0
    TTS_TIMEOUT = 90.0
    PLAYBACK_TIMEOUT = 60.0
    MIN_INTERVAL = 2.0
    CLOUD_BUDGET = 100

    def __init__(self, settings, keys, session_id, mode="live",
                 preview_text="Hola, esta es una prueba de voz.", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.keys = dict(keys)
        self.session_id = session_id
        self.mode = mode
        self.preview_text = preview_text
        self._stop_requested = threading.Event()
        self._loop = None
        self._main_task = None
        self._queue = None
        self._dictations = None
        self._playback = None
        self._playback_ok = True
        self._policy = Policy(settings)
        self._overflow = 0
        self._overflow_reported = 0.0
        self._admitted = 0
        self._last_cloud = None
        self._budget_reported = False
        self._mic_active = False
        self._memory = None
        self._room_key = str(settings.handle).strip().lstrip('@').casefold() or str(session_id)
        self._voice = None
        self._join_warned = False
        self._join_storage_error_reported = False

    def _emit(self, signal, *args):
        if not self._stop_requested.is_set():
            signal.emit(self.session_id, *args)

    def _schedule(self, callback, *args):
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(callback, *args)

    def stop(self):
        self._stop_requested.set()
        loop, task, playback = self._loop, self._main_task, self._playback
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._cancel_on_loop, task, playback)

    @staticmethod
    def _cancel_on_loop(task, playback):
        if playback is not None:
            playback.set()
        if task is not None and not task.done():
            task.cancel()

    def acknowledge(self, success=True):
        self._schedule(self._acknowledge, bool(success))

    def _acknowledge(self, success=True):
        if self._playback is not None:
            self._playback_ok = success
            self._playback.set()

    def inject(self, kind, user_id, name, text=""):
        if self.mode == "demo" and not self._stop_requested.is_set():
            self._schedule(self._enqueue, Event(kind, str(user_id), name, text, time.monotonic()))

    def inject_dictation(self, text):
        if not self._stop_requested.is_set():
            self._schedule(self._enqueue_dictation, text)

    def set_mic_active(self, active):
        self._schedule(setattr, self, "_mic_active", bool(active))

    def _enqueue_dictation(self, text):
        if self._dictations is not None and not self._stop_requested.is_set():
            text = str(text).strip()[:1000]
            if text:
                if self._dictations.full():
                    self._dictations.get_nowait()
                    self._emit(self.log, "Se omitió el dictado más antiguo porque la cola de voz está llena.")
                self._dictations.put_nowait(text)

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 - logs exclude exception values and user data
            record_exception('worker thread failed', exc)
            self._emit(self.log, "La sesión terminó por un error interno. Puedes volver a iniciarla.")
        finally:
            self._loop = None
            self._main_task = None
            self.keys.clear()
            self.status.emit(self.session_id, "Detenido")

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        self._queue = asyncio.Queue(maxsize=self.QUEUE_SIZE)
        self._dictations = asyncio.Queue(maxsize=3)
        self._playback = asyncio.Event()
        if self.settings.remember_context:
            try:
                self._memory = MemoryStore()
                memory = self._memory.context()
                self.settings.activity_context = memory.get('activity') or self.settings.activity_context
                if memory.get('facts'):
                    self.settings.activity_context += f"\nDatos recordados: {memory['facts']}"
            except (OSError, sqlite3.Error):
                self._emit(self.log, "No se pudo abrir la memoria local; se usa solo el contexto de esta sesión.")
        if self._stop_requested.is_set():
            return
        try:
            if self.mode == "demo":
                self._emit(self.status, "Demo activa (sin servicios de nube)")
                await self._initial_greeting(None)
                await self._consume(None)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                    services = GoogleServices(client, self.keys, self.settings)
                    if self.mode == "live":
                        await self._live(services)
                    elif self.mode == "preview":
                        self._emit(self.status, "Preparando prueba de voz…")
                        data = await asyncio.wait_for(services.synthesize(self.preview_text), self.TTS_TIMEOUT)
                        await self._play(self.preview_text, data)
                    elif self.mode == "preview_local":
                        self._emit(self.status, "Preparando voz local (primera descarga puede tardar)…")
                        data = await asyncio.wait_for(self._synthesize_local(self.preview_text), self.TTS_TIMEOUT)
                        await self._play(self.preview_text, data)
                    elif self.mode in {"voices", "models", "local_models"}:
                        values = await asyncio.wait_for(getattr(services, self.mode)(), self.API_TIMEOUT)
                        self._emit(self.catalogue, self.mode, values)
                    else:
                        self._emit(self.log, "Modo de sesión desconocido.")
        except asyncio.CancelledError:
            pass
        except ProviderError as exc:
            self._emit(self.log, str(exc))
        except QwenDependencyError as exc:
            record_exception('Qwen dependency missing', exc)
            self._emit(self.log, str(exc))
        except QwenResourceError as exc:
            record_exception('Qwen resources unavailable', exc)
            self._emit(self.log, str(exc))
        except TimeoutError:
            self._emit(self.log, "Se agotó el tiempo de espera. Intenta iniciar de nuevo.")
        except Exception as exc:  # noqa: BLE001 - logs exclude exception values and user data
            record_exception('session startup failed', exc)
            self._emit(self.log, "No se pudo completar la sesión. Comprueba la conexión y la configuración.")
        finally:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()

    def _enqueue(self, event):
        if self._stop_requested.is_set() or self._queue is None:
            return
        if event.kind == "join":
            if not self.settings.greetings:
                return
            name = clean_text(event.name, 60) or 'visitante'
            blocked = [word.strip().casefold() for word in self.settings.blocked_words.replace(';', ',')
                       .replace('\n', ',').split(',') if word.strip()]
            if not event.user_id or any(word in name.casefold() for word in blocked):
                return
            try:
                if self._memory is None:
                    self._memory = MemoryStore()
                self._memory.enqueue_join(self._room_key, event.user_id, name)
            except (OSError, ValueError, sqlite3.Error):
                self._emit(self.log, "No se pudo guardar una bienvenida pendiente en la memoria local.")
            return
        if self._uses_cloud(event) and self._admitted >= self.CLOUD_BUDGET and self.mode != "demo":
            self._report_budget()
            return
        if self._queue.full():
            self._overflow += 1
            now = time.monotonic()
            if now - self._overflow_reported >= 2:
                self._emit(self.log, f"Cola llena: {self._overflow} eventos nuevos descartados.")
                self._overflow_reported = now
            return
        event, _reason = self._policy.accept(event)
        if event is not None:
            self._queue.put_nowait(event)

    def _report_budget(self):
        if not self._budget_reported:
            self._budget_reported = True
            self._emit(self.log, "Límite de 100 intervenciones de nube alcanzado. Detén e inicia otra sesión para continuar.")

    async def _consume(self, services):
        if self._dictations is None:
            self._dictations = asyncio.Queue(maxsize=3)
        while not self._stop_requested.is_set():
            event = None
            viewer_id = None
            if self._mic_active:
                await asyncio.sleep(0.15)
                continue
            if self.settings.greetings:
                try:
                    if self._memory is None:
                        self._memory = MemoryStore()
                    pending_count, pending = self._memory.next_pending_join(self._room_key)
                    if pending_count >= 800 and not self._join_warned:
                        self._join_warned = True
                        self._emit(self.log, "Hay 800 o más bienvenidas pendientes; se están procesando antes que los comentarios.")
                    if pending:
                        viewer_id, name = pending
                        event = Event("join", viewer_id, name, created=time.monotonic())
                except (OSError, sqlite3.Error):
                    if not self._join_storage_error_reported:
                        self._join_storage_error_reported = True
                        self._emit(self.log, "No se pudo consultar la cola de bienvenidas guardada.")
            if event is None and not self._dictations.empty():
                text = self._dictations.get_nowait()
                try:
                    data = await self._synthesize_local(text)
                    await self._play(text, data)
                except QwenDependencyError as exc:
                    self._emit(self.log, str(exc))
                except Exception:  # noqa: BLE001
                    self._emit(self.log, "No se pudo sintetizar el dictado con la voz local.")
                continue
            if event is None:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=0.25)
                except TimeoutError:
                    continue
            try:
                if event.kind != "join" and time.monotonic() - event.created > self.MAX_AGE:
                    continue
                text = self._policy.greeting(event) if event.kind == "join" else event.text
                if self.mode == "demo":
                    try:
                        played = await self._play(text, await self._synthesize_local(text))
                        if event.kind == "join" and self._memory and played:
                            self._memory.mark_greeted(self._room_key, viewer_id)
                        elif event.kind == 'join' and not played:
                            self._emit(self.log, "No se confirmó el audio del saludo; se mantiene en cola para reintentar.")
                            await asyncio.sleep(5)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - keep the demo window alive on engine errors
                        record_exception('demo event synthesis failed', exc)
                        self._emit(self.log, self._voice_error_message(exc))
                    continue
                if self._uses_cloud(event) and self._admitted >= self.CLOUD_BUDGET and event.kind != "join":
                    self._report_budget()
                    continue
                if event.kind != 'join' and self._uses_cloud(event) and self._last_cloud is not None:
                    await asyncio.sleep(max(0, self.MIN_INTERVAL - (time.monotonic() - self._last_cloud)))
                if event.kind != "join" and time.monotonic() - event.created > self.MAX_AGE:
                    continue
                # Reserve before generation: failures still consume this bounded budget.
                use_cloud = self._uses_cloud(event) and not (
                    event.kind == 'join' and self._admitted >= self.CLOUD_BUDGET)
                if use_cloud:
                    self._admitted += 1
                    self._last_cloud = time.monotonic()
                if event.kind == "comment" and self.settings.mode == "answer":
                    try:
                        text = await asyncio.wait_for(services.reply(event.name, event.text), self.API_TIMEOUT)
                    except (ProviderError, TimeoutError) as exc:
                        self._emit(self.log, f"No se pudo generar una respuesta para este comentario: {exc}")
                        continue
                    if time.monotonic() - event.created > self.MAX_AGE:
                        self._emit(self.log, "La respuesta llegó tarde; se omite para no interrumpir el directo.")
                        continue
                try:
                    if event.kind == 'join' and not use_cloud and self.settings.tts_engine == 'gemini':
                        data = await asyncio.wait_for(self._synthesize_local(text), self.TTS_TIMEOUT)
                    else:
                        data = await asyncio.wait_for(self._synthesize(text, services), self.TTS_TIMEOUT)
                except (ProviderError, TimeoutError) as exc:
                    if event.kind == 'join' and self.settings.tts_engine == 'gemini':
                        try:
                            data = await asyncio.wait_for(self._synthesize_local(text), self.TTS_TIMEOUT)
                        except Exception:  # noqa: BLE001
                            self._emit(self.log, "No se pudo generar una bienvenida local; se mantiene en cola para reintentar.")
                            await asyncio.sleep(5)
                            continue
                    else:
                        self._emit(self.log, f"No se pudo sintetizar este mensaje; no se repetirá el comentario: {exc}")
                        continue
                except QwenDependencyError as exc:
                    record_exception('Qwen dependency missing during narration', exc)
                    self._emit(self.log, str(exc))
                    if event.kind == 'join':
                        await asyncio.sleep(5)
                    continue
                except QwenResourceError as exc:
                    record_exception('Qwen resources unavailable during narration', exc)
                    self._emit(self.log, str(exc))
                    if event.kind == 'join':
                        await asyncio.sleep(5)
                    continue
                except Exception:  # noqa: BLE001 - local runtime can fail without provider context
                    if event.kind == 'join':
                        self._emit(self.log, "No se pudo generar una bienvenida local; se mantiene en cola para reintentar.")
                        await asyncio.sleep(5)
                    else:
                        self._emit(self.log, "No se pudo sintetizar este mensaje; no se repetirá el comentario.")
                    continue
                if event.kind != "join" and time.monotonic() - event.created > self.MAX_AGE:
                    self._emit(self.log, "El audio llegó tarde; se omite para no interrumpir el directo.")
                    continue
                played = await self._play(text, data)
                if event.kind == "join" and self._memory and played:
                    self._memory.mark_greeted(self._room_key, viewer_id)
                elif event.kind == 'join' and not played:
                    self._emit(self.log, "No se confirmó el audio del saludo; se mantiene en cola para reintentar.")
                    await asyncio.sleep(5)
            finally:
                if event.kind != "join":
                    self._queue.task_done()

    async def _synthesize(self, text, services):
        if self.settings.tts_engine in ('local', 'qwen') or self.mode == 'demo':
            return await self._synthesize_local(text)
        return await services.synthesize(text)

    @staticmethod
    def _voice_error_message(error):
        if isinstance(error, (QwenDependencyError, QwenResourceError)):
            return str(error)
        return 'Falló la voz de la demo. La ventana sigue abierta; consulta los registros técnicos.'

    async def _initial_greeting(self, services):
        text = clean_text(self.settings.initial_greeting, 400)
        if not text:
            return
        try:
            data = await asyncio.wait_for(self._synthesize(text, services), self.TTS_TIMEOUT)
            played = await self._play(text, data)
            LOGGER.info('initial_greeting.played success=%s engine=%s', played, self.settings.tts_engine)
        except asyncio.CancelledError:
            raise
        except InterruptedError:
            LOGGER.info('initial_greeting.cancelled')
        except Exception as exc:  # noqa: BLE001 - a greeting failure must not stop the live session
            record_exception('initial AI greeting failed', exc)
            self._emit(self.log, self._voice_error_message(exc))

    def _uses_cloud(self, event):
        tts_cloud = self.settings.tts_engine == 'gemini'
        reply_cloud = (event.kind == 'comment' and self.settings.mode == 'answer'
                       and self.settings.reply_provider == 'gemini')
        return self.mode != 'demo' and (tts_cloud or reply_cloud)

    async def _synthesize_local(self, text):
        if self._voice is None:
            engine = QwenVoice if self.settings.tts_engine == 'qwen' else LocalVoice
            LOGGER.info('local_tts.load.begin engine=%s', self.settings.tts_engine)
            last = {}
            def progress(name, current, total):
                percent = min(100, current * 100 // max(1, total))
                previous = last.get(name, -5)
                if percent >= previous + 5 or percent == 100:
                    last[name] = percent
                    self._emit(self.status, f"Descargando voz local · {percent}%")
            if engine is QwenVoice:
                self._emit(self.status, 'Descargando Qwen3-TTS 1.7B (aprox. 4,5 GB) y preparando la voz…')
                def qwen_progress(name, current, total):
                    percent = min(100, current * 100 // max(1, total))
                    previous = last.get(name, -5)
                    if percent >= previous + 5 or percent == 100:
                        last[name] = percent
                        self._emit(self.status, f'Descargando Qwen3-TTS · {percent}%')
                self._voice = engine(qwen_progress, self._stop_requested)
            else:
                self._emit(self.status, 'Descargando el modelo de voz local…')
                self._voice = engine(None, progress, self._stop_requested)
            LOGGER.info('local_tts.load.ready engine=%s', self.settings.tts_engine)
        if isinstance(self._voice, QwenVoice):
            return self._voice.synthesize(
                text, self.settings.qwen_accent, self.settings.qwen_style,
                self.settings.pitch, self.settings.gender,
            )
        return self._voice.synthesize(text, self.settings.local_voice, self.settings.rate)

    async def _play(self, text, data=None):
        if self._stop_requested.is_set():
            return False
        self._playback.clear()
        self._playback_ok = True
        self._emit(self.spoken, text)
        if self.mode == "demo" and data is None:
            self._emit(self.demo_speech, text)
        else:
            self._emit(self.audio, data)
        try:
            await asyncio.wait_for(self._playback.wait(), self.PLAYBACK_TIMEOUT)
        except TimeoutError:
            # Do not start more audio while the previous playback might still be active.
            self._emit(self.log, "El reproductor no confirmó el final del audio. La sesión se detuvo.")
            raise
        return self._playback_ok

    async def _live(self, services):
        from TikTokLive import TikTokLiveClient
        from TikTokLive.client.web.web_settings import WebDefaults
        from TikTokLive.events import (
            CommentEvent,
            ConnectEvent,
            DisconnectEvent,
            JoinEvent,
        )

        connected = asyncio.Event()
        disconnected = asyncio.Event()
        client = None
        tasks = []
        old_key = WebDefaults.tiktok_sign_api_key
        try:
            WebDefaults.tiktok_sign_api_key = self.keys.get("euler") or None
            client = TikTokLiveClient(unique_id=self.settings.handle)
            # Provider diagnostics can contain signed URLs. Only fixed messages leave this adapter.
            client.logger.disabled = True

            async def on_connect(_event):
                room_id = getattr(_event, 'room_id', None) or getattr(client, 'room_id', None)
                if room_id:
                    self._room_key = f"{self.settings.handle.strip().lstrip('@').casefold()}:{room_id}"
                    try:
                        if self._memory is None:
                            self._memory = MemoryStore()
                        self._memory.prune_greeted()
                        self._memory.keep_room_only(self._room_key)
                    except (OSError, sqlite3.Error):
                        self._emit(self.log, "No se pudieron limpiar las bienvenidas de directos anteriores.")
                else:
                    handle = self.settings.handle.strip().lstrip('@').casefold()
                    self._room_key = f'{handle}:session-{self.session_id}'
                    self._emit(self.log, "TikTok no proporcionó el identificador del directo; los saludos pendientes no podrán recuperarse entre sesiones.")
                connected.set()
                self._emit(self.status, "Conectado a TikTok LIVE")

            async def on_disconnect(_event):
                disconnected.set()
                self._emit(self.status, "Desconectado de TikTok LIVE")

            async def on_comment(event):
                self._receive_live("comment", event)

            async def on_join(event):
                self._receive_live("join", event)

            client.add_listener(ConnectEvent, on_connect)
            client.add_listener(DisconnectEvent, on_disconnect)
            client.add_listener(CommentEvent, on_comment)
            client.add_listener(JoinEvent, on_join)
            self._emit(self.status, "Conectando a TikTok LIVE…")
            deadline = time.monotonic() + self.CONNECT_TIMEOUT
            connection_task = await asyncio.wait_for(client.start(), self.CONNECT_TIMEOUT)
            tasks.append(connection_task)
            connect_wait = asyncio.create_task(connected.wait())
            tasks.append(connect_wait)
            done, _ = await asyncio.wait(
                (connection_task, connect_wait),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if connection_task in done:
                await connection_task
                raise ConnectionError
            if not connected.is_set():
                raise TimeoutError
            await self._initial_greeting(services)
            consumer = asyncio.create_task(self._consume(services))
            disconnect_wait = asyncio.create_task(disconnected.wait())
            tasks.extend((consumer, disconnect_wait))
            done, _ = await asyncio.wait((connection_task, consumer, disconnect_wait),
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
            self._emit(self.log, "La conexión terminó. Inicia de nuevo para reconectar.")
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._emit(self.log, "TikTok o el reproductor no respondieron a tiempo. Inicia de nuevo manualmente.")
        except Exception as exc:  # noqa: BLE001 - logs exclude exception values and user data
            record_exception('TikTok LIVE connection failed', exc)
            self._emit(self.log, "No se pudo mantener TikTok LIVE. Comprueba que la cuenta esté en directo, la red y el acceso de firma Euler.")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if client is not None:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(client.disconnect(close_client=True), 5.0)
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(client.close(), 5.0)
            WebDefaults.tiktok_sign_api_key = old_key

    def _receive_live(self, kind, event):
        user = getattr(event, "user", None)
        identity = getattr(user, "id", None) or getattr(user, "unique_id", None)
        if identity is None:
            if kind == 'join':
                self._emit(self.log, "TikTok envió una llegada sin ID; no se puede guardar su saludo de forma persistente.")
            return
        self._enqueue(Event(kind, str(identity), getattr(user, "nickname", "Usuario"),
                            getattr(event, "comment", "") if kind == "comment" else "",
                            time.monotonic()))
