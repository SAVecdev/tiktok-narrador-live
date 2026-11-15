"""Opt-in, local-only microphone VAD and Spanish transcription."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal


class MicWorker(QThread):
    transcript = Signal(str)
    state = Signal(str)
    speech_active = Signal(bool)

    RATE = 16_000
    BLOCK = 1024
    THRESHOLD = 0.012
    SILENCE_SECONDS = 0.7
    MAX_SECONDS = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._paused = threading.Event()

    def stop(self):
        self._stop.set()

    def set_paused(self, paused):
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def run(self):
        import numpy as np

        try:
            if self._stop.is_set():
                return
            from faster_whisper import WhisperModel
            model = self._load_model(WhisperModel)
            if self._stop.is_set():
                return
            import sounddevice as sd
            self.state.emit('Micrófono activo · audio procesado localmente')
            silence_limit = int(self.SILENCE_SECONDS * self.RATE / self.BLOCK)
            maximum = int(self.MAX_SECONDS * self.RATE / self.BLOCK)
            frames = []
            silence = 0
            active = False
            with sd.InputStream(samplerate=self.RATE, channels=1, dtype='float32',
                                blocksize=self.BLOCK, latency='low') as stream:
                while not self._stop.is_set():
                    data, overflowed = stream.read(self.BLOCK)
                    if self._paused.is_set():
                        if active:
                            active = False
                            frames = []
                            silence = 0
                            self.speech_active.emit(False)
                        continue
                    if overflowed:
                        self.state.emit('El micrófono perdió algunos cuadros; continúa escuchando.')
                    frame = np.asarray(data, dtype=np.float32).reshape(-1)
                    level = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0
                    if level >= self.THRESHOLD:
                        if not active:
                            active = True
                            frames = []
                            self.speech_active.emit(True)
                        silence = 0
                        frames.append(frame.copy())
                    elif active:
                        frames.append(frame.copy())
                        silence += 1
                    if active and (silence >= silence_limit or len(frames) >= maximum):
                        audio = np.concatenate(frames)
                        active = False
                        frames = []
                        silence = 0
                        try:
                            segments, _info = model.transcribe(audio, language='es', beam_size=1,
                                                               vad_filter=True, condition_on_previous_text=False)
                            text = ' '.join(segment.text.strip() for segment in segments).strip()
                            if text and not self._stop.is_set():
                                self.transcript.emit(text[:1000])
                        except Exception:  # noqa: BLE001
                            self.state.emit('No se entendió la frase; intenta hablar más cerca del micrófono.')
                        self.speech_active.emit(False)
            self.state.emit('Micrófono detenido')
        except InterruptedError:
            self.state.emit('Micrófono detenido')
        except Exception as exc:  # noqa: BLE001 - never return raw provider/device errors
            reason = 'Permite el acceso al micrófono y comprueba que haya un dispositivo de entrada.'
            if 'faster_whisper' in str(exc).casefold() or 'ctranslate' in str(exc).casefold():
                reason = 'No se pudo iniciar la transcripción local. Revisa la instalación de faster-whisper.'
            self.state.emit(reason)
        finally:
            self.speech_active.emit(False)

    def _load_model(self, whisper_model):
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm

        worker = self

        class CancellableTqdm(tqdm):
            def update(self, n=1):
                if worker._stop.is_set():
                    raise InterruptedError('ASR download cancelled.')
                result = super().update(n)
                total = self.total or 0
                if total:
                    percent = int(self.n * 100 / total)
                    if percent % 10 == 0:
                        worker.state.emit(f'Descargando transcripción local: {percent}%')
                return result

        if self._stop.is_set():
            raise InterruptedError('ASR download cancelled.')
        self.state.emit('Preparando transcripción local (primera vez descarga ~75 MB)…')
        model_path = snapshot_download(
            'Systran/faster-whisper-tiny',
            revision='d90ca5fe260221311c53c58e660288d3deb8d356',
            allow_patterns=['config.json', 'preprocessor_config.json', 'model.bin',
                            'tokenizer.json', 'vocabulary.*'],
            max_workers=1, tqdm_class=CancellableTqdm,
        )
        if self._stop.is_set():
            raise InterruptedError('ASR download cancelled.')
        return whisper_model(model_path, device='cpu', compute_type='int8')
