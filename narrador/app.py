"""Native desktop interface; credentials and activity never enter log files."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from platformdirs import user_config_path
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QSlider, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from .config import SecretStore, Settings, load_settings, save_settings
from .memory import MemoryStore
from .microphone import MicWorker
from .worker import SessionWorker

STYLE = """
QWidget { background: #101a2d; color: #e7edf6; font-size: 14px; }
QMainWindow { background: #101a2d; }
QLabel#eyebrow { color: #57ddcb; font-size: 12px; font-weight: 700; }
QLabel#title { font-size: 30px; font-weight: 800; }
QLabel#subtitle, QLabel#hint { color: #a9b8ce; }
QLabel#status { background: #203149; border-radius: 10px; padding: 12px; color: #8ce5d8; }
QTabWidget::pane { border: 1px solid #30415d; border-radius: 9px; padding: 12px; }
QTabBar::tab { background: #19263d; padding: 11px 23px; }
QTabBar::tab:selected { color: #61e2ce; border-bottom: 2px solid #61e2ce; }
QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit { background: #19263d; border: 1px solid #3c4d68; border-radius: 6px; padding: 7px; min-height: 32px; selection-background-color: #267c80; }
QPushButton { background: #253852; border: 1px solid #405571; border-radius: 7px; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #334d6e; }
QPushButton#primary { background: #55dcc5; color: #102b32; border: 0; }
QPushButton#stop { color: #ffb6bc; }
QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled { color: #75849b; }
QSlider::groove:horizontal { height: 5px; background: #30415d; }
QSlider::handle:horizontal { background: #55dcc5; width: 16px; margin: -6px 0; border-radius: 8px; }
QCheckBox { spacing: 9px; padding: 4px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, settings_path=None, secret_store=None, worker_factory=None):
        super().__init__()
        self.settings_path = Path(settings_path or user_config_path('TikTokNarradorLive') / 'settings.json')
        self.secret_store = secret_store or SecretStore()
        self.worker_factory = worker_factory or SessionWorker
        self.worker = None
        self.session_id = 0
        self.closing = False
        self.audio_path = None
        self.voices = []
        self.mic_worker = None
        self.setWindowTitle('TikTok Narrador LIVE')
        self.resize(1030, 880)
        self.setMinimumSize(760, 740)
        self.setStyleSheet(STYLE)
        self.player = QMediaPlayer(self)
        self.output = None
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self.tts = None
        settings, warning = load_settings(self.settings_path)
        keys, key_warning = self.secret_store.load()
        self._build(settings, keys)
        if settings.remember_context:
            try:
                memory = MemoryStore().context()
                if memory['activity']:
                    self.activity_context.setText(memory['activity'])
                if memory['facts']:
                    self.memory_facts.setPlainText(memory['facts'])
            except (OSError, sqlite3.Error):
                self._log('No se pudo cargar la memoria local.')
        if warning:
            self._log(warning)
        if key_warning:
            self._log(key_warning)

    def _button(self, text, callback, name=None):
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def _build(self, settings, keys):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        eyebrow = QLabel('TU COMUNIDAD, EN VOZ ALTA')
        eyebrow.setObjectName('eyebrow')
        layout.addWidget(eyebrow)
        title = QLabel('Narrador LIVE')
        title.setObjectName('title')
        layout.addWidget(title)
        subtitle = QLabel('Comentarios y bienvenidas para tu directo de TikTok.')
        subtitle.setObjectName('subtitle')
        layout.addWidget(subtitle)
        self.status = QLabel('●  Listo · prueba la demo sin credenciales')
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        def scroll_page(title):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            scroll.setWidget(content)
            self.tabs.addTab(scroll, title)
            return content
        voice_page = scroll_page('Voz')
        behavior_page = scroll_page('Comportamiento')
        api_page = scroll_page('API y privacidad')
        voice_form = QFormLayout(voice_page)
        self.tts_engine = QComboBox()
        self.tts_engine.addItem('Local · Qwen3-TTS 1.7B (voz diseñada)', 'qwen')
        self.tts_engine.addItem('Local · Kokoro (ligero, sin API)', 'local')
        self.tts_engine.addItem('Google AI Studio · Gemini', 'gemini')
        self.tts_engine.setCurrentIndex(max(0, self.tts_engine.findData(settings.tts_engine)))
        self.language = QLineEdit(settings.language)
        self.gender = QComboBox()
        self.gender.addItem('Femenina', 'FEMALE')
        self.gender.addItem('Masculina', 'MALE')
        self.gender.addItem('Neutra', 'NEUTRAL')
        self.gender.setCurrentIndex(max(0, self.gender.findData(settings.gender)))
        self.voice = QComboBox()
        self.voice.addItem('Automática según idioma y género', '')
        if settings.voice:
            self.voice.addItem(settings.voice, settings.voice)
            self.voice.setCurrentIndex(1)
        self.local_voice = QComboBox()
        self.local_voice.addItem('Dora · femenina, español', 'ef_dora')
        self.local_voice.setCurrentIndex(max(0, self.local_voice.findData(settings.local_voice)))
        self.qwen_accent = QComboBox()
        self.qwen_accent.addItem('Colombia · Medellín (paisa suave)', 'colombia_medellin')
        self.qwen_accent.addItem('Colombia · Bogotá', 'colombia_bogota')
        self.qwen_accent.addItem('Latinoamericano neutral', 'latino_neutral')
        self.qwen_accent.setCurrentIndex(max(0, self.qwen_accent.findData(settings.qwen_accent)))
        self.qwen_style = QLineEdit(settings.qwen_style)
        self.gender.currentIndexChanged.connect(self._filter_voices)
        self.language.textChanged.connect(self._filter_voices)
        self.pitch = QDoubleSpinBox()
        self.pitch.setRange(-20, 20)
        self.pitch.setValue(settings.pitch)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(.25, 4)
        self.rate.setSingleStep(.1)
        self.rate.setValue(settings.rate)
        voice_form.addRow('Motor de voz', self.tts_engine)
        voice_form.addRow('Voz ligera (Kokoro)', self.local_voice)
        voice_form.addRow('Acento Qwen3-TTS', self.qwen_accent)
        voice_form.addRow('Estilo Qwen (editable)', self.qwen_style)
        voice_form.addRow('Voz Gemini TTS', self.voice)
        voice_form.addRow('Idioma (p. ej., es-US)', self.language)
        voice_form.addRow('Género de voz', self.gender)
        voice_form.addRow('Tono (−20 a +20)', self.pitch)
        voice_form.addRow('Velocidad (0,25 a 4)', self.rate)
        voice_note = QLabel('Qwen3-TTS diseña la voz según género, tonalidad y estilo. Puedes solicitar acento de Medellín o Bogotá, pero son descripciones, no grabaciones colombianas; el resultado puede variar. La primera ejecución descarga unos 4,5 GB y requiere instalar requirements-qwen.txt. El dictado se sintetiza con voz IA; no clona tu identidad vocal. Kokoro es la alternativa ligera.')
        voice_note.setWordWrap(True)
        voice_note.setObjectName('hint')
        voice_form.addRow(voice_note)
        voice_actions = QHBoxLayout()
        voice_actions.addWidget(self._button('Actualizar voces', lambda: self._start('voices')))
        voice_actions.addWidget(self._button('▶ Probar voz seleccionada', self._preview_selected))
        voice_form.addRow(voice_actions)
        self.mic_button = self._button('🎙 Activar micrófono y repetir con IA', self._toggle_microphone)
        voice_form.addRow(self.mic_button)
        self.mic_status = QLabel('Micrófono apagado. Al activarlo, el audio se transcribe localmente y se descarta.')
        self.mic_status.setWordWrap(True)
        self.mic_status.setObjectName('hint')
        voice_form.addRow(self.mic_status)
        self.tts_engine.currentIndexChanged.connect(self._tts_changed)
        self._tts_changed()
        form = QFormLayout(behavior_page)
        self.mode = QComboBox()
        for label, value in [('Leer comentarios', 'read'), ('Responder con IA', 'answer'), ('Ignorar comentarios', 'ignore')]:
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.mode)))
        self.greetings = QCheckBox('Dar la bienvenida a quienes se unen')
        self.greetings.setChecked(settings.greetings)
        self.greeting = QLineEdit(settings.greeting_template)
        self.blocked = QLineEdit(settings.blocked_words)
        self.persona = QLineEdit(settings.persona)
        self.activity_context = QLineEdit(settings.activity_context)
        self.activity_context.setPlaceholderText('Ej.: Estoy jugando Minecraft y construyendo una casa')
        self.memory_facts = QTextEdit()
        self.memory_facts.setPlaceholderText('Datos que la IA debe recordar: mapa, objetivo, reglas del juego…')
        self.memory_facts.setMaximumHeight(90)
        self.remember_context = QCheckBox('Guardar contexto en una base local para próximos directos')
        self.remember_context.setChecked(settings.remember_context)
        self.allow_context_cloud = QCheckBox('Permitir enviar actividad y memoria a Gemini al responder')
        self.allow_context_cloud.setChecked(settings.allow_context_cloud)
        self.reply_provider = QComboBox()
        self.reply_provider.addItem('IA local · Ollama (privada)', 'ollama')
        self.reply_provider.addItem('Gemini API · Google AI Studio', 'gemini')
        self.reply_provider.setCurrentIndex(max(0, self.reply_provider.findData(settings.reply_provider)))
        self.ollama_url = QLineEdit(settings.ollama_url)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(settings.model)
        form.addRow('Comentarios', self.mode)
        form.addRow('', self.greetings)
        form.addRow('Saludo · usa {usuario}', self.greeting)
        form.addRow('Palabras bloqueadas', self.blocked)
        form.addRow('Personalidad de la IA', self.persona)
        form.addRow('¿Qué estás haciendo en el directo?', self.activity_context)
        form.addRow('Contexto adicional persistente', self.memory_facts)
        form.addRow('', self.remember_context)
        form.addRow('', self.allow_context_cloud)
        form.addRow('Proveedor de respuestas', self.reply_provider)
        form.addRow('Dirección local de Ollama', self.ollama_url)
        form.addRow('Modelo', self.model)
        form.addRow(self._button('Actualizar modelos disponibles', self._refresh_models))
        form.addRow(self._button('Exportar contexto', self._export_memory))
        form.addRow(self._button('Borrar contexto y hechos guardados', self._clear_memory))
        api_form = QFormLayout(api_page)
        self.keys = {}
        for key, label in [('gemini', 'Google AI Studio · Gemini TTS'), ('euler', 'Euler Stream (opcional)')]:
            field = QLineEdit(keys.get(key, ''))
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText('Clave API')
            self.keys[key] = field
            api_form.addRow(label, field)
        self.remember = QCheckBox('Recordar claves en el almacén seguro del sistema')
        self.remember.setChecked(settings.remember)
        api_form.addRow(self.remember)
        privacy = QLabel('Las claves solo se guardan al pulsar Guardar.\nKokoro y Whisper procesan voz localmente. El audio del micrófono no se guarda ni se envía; el dictado se pronuncia con la voz sintética local.\nOllama responde en tu equipo. Gemini solo recibe el contexto si eliges Google para responder y activas el permiso en Comportamiento. Gemini TTS recibe el texto que sintetiza. La demo usa voz local.')
        privacy.setWordWrap(True)
        privacy.setObjectName('hint')
        api_form.addRow(privacy)
        controls = QHBoxLayout()
        self.handle = QLineEdit(settings.handle)
        self.handle.setPlaceholderText('@usuario de TikTok')
        self.handle.setAccessibleName('Usuario de TikTok LIVE')
        controls.addWidget(self.handle, 1)
        self.start_button = self._button('Iniciar LIVE', lambda: self._start('live'), 'primary')
        self.demo_button = self._button('Demo', lambda: self._start('demo'))
        self.stop_button = self._button('■ Detener', self._stop, 'stop')
        self.stop_button.setEnabled(False)
        self.save_button = self._button('Guardar', self._save)
        for button in [self.start_button, self.demo_button, self.stop_button, self.save_button]:
            controls.addWidget(button)
        layout.addLayout(controls)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel('Volumen'))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(round(settings.volume * 100))
        self.volume.setAccessibleName('Volumen de reproducción')
        self.volume.valueChanged.connect(self._volume_changed)
        self._volume_changed(self.volume.value())
        volume_row.addWidget(self.volume, 1)
        self.sim_comment = self._button('Simular comentario', lambda: self._inject('comment'))
        self.sim_join = self._button('Simular llegada', lambda: self._inject('join'))
        self.sim_comment.setEnabled(False)
        self.sim_join.setEnabled(False)
        volume_row.addWidget(self.sim_comment)
        volume_row.addWidget(self.sim_join)
        layout.addLayout(volume_row)
        activity_header = QHBoxLayout()
        activity_header.addWidget(QLabel('ACTIVIDAD DE LA SESIÓN'))
        activity_header.addStretch()
        activity_header.addWidget(QLabel('Solo en memoria · últimas 300 líneas'))
        layout.addLayout(activity_header)
        self.activity = QTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setAcceptRichText(False)
        self.activity.document().setMaximumBlockCount(300)
        self.activity.setAccessibleName('Actividad de la sesión')
        self.activity.setMinimumHeight(130)
        layout.addWidget(self.activity, 1)
        self._log('Bienvenido. Abre Demo y simula un comentario o una llegada para comenzar.')

    def _settings(self):
        return Settings(handle=self.handle.text(), language=self.language.text(), gender=self.gender.currentData(),
                        voice=self.voice.currentData() or '', pitch=self.pitch.value(), rate=self.rate.value(),
                        volume=self.volume.value() / 100, mode=self.mode.currentData(), greetings=self.greetings.isChecked(),
                        greeting_template=self.greeting.text(), blocked_words=self.blocked.text(), model=self.model.currentText(), reply_provider=self.reply_provider.currentData(),
                        ollama_url=self.ollama_url.text().rstrip('/'), persona=self.persona.text(), activity_context=self.activity_context.text(), remember=self.remember.isChecked(),
                        remember_context=self.remember_context.isChecked(), tts_engine=self.tts_engine.currentData(),
                        allow_context_cloud=self.allow_context_cloud.isChecked(),
                        local_voice=self.local_voice.currentData(), qwen_accent=self.qwen_accent.currentData(),
                        qwen_style=self.qwen_style.text(), mic_enabled=self.mic_worker is not None).validate()

    def _save(self):
        try:
            settings = self._settings()
            persisted = settings
            if not settings.remember_context:
                persisted.activity_context = ''
            save_settings(persisted, self.settings_path)
            if settings.remember_context:
                memory = MemoryStore()
                memory.set_activity(self.activity_context.text())
                memory.set_facts(self.memory_facts.toPlainText())
            else:
                MemoryStore().clear_context()
            warning = self.secret_store.save(self._key_values(), remember=settings.remember)
            self._log(warning or 'Preferencias guardadas. Claves protegidas en el almacén seguro.' if settings.remember else warning or 'Preferencias guardadas. Claves solo para esta sesión.')
        except (ValueError, OSError, sqlite3.Error):
            self._log('No se pudo guardar. Revisa los valores y los permisos de la carpeta de configuración.')

    def _key_values(self):
        values = {name: field.text().strip() for name, field in self.keys.items()}
        values['tts'] = values.get('gemini', '')
        return values

    def _start(self, mode):
        if self.worker is not None:
            return
        try:
            settings = self._settings()
        except ValueError:
            self._log('Revisa los valores de configuración antes de continuar.')
            return
        keys = self._key_values()
        if mode == 'live' and not settings.handle.strip().lstrip('@'):
            self._log('Escribe el usuario de TikTok para iniciar LIVE.')
            return
        if mode == 'live' and settings.tts_engine == 'gemini' and not keys['gemini']:
            self._log('Introduce la clave de Google AI Studio para Gemini TTS en API y privacidad.')
            return
        if mode == 'live' and settings.reply_provider == 'gemini' and not keys['gemini']:
            self._log('Introduce una clave de Google AI Studio para las respuestas Gemini.')
            return
        if mode == 'models' and settings.reply_provider == 'gemini' and not keys['gemini']:
            self._log('Introduce una clave de Gemini en API y privacidad.')
            return
        self.session_id += 1
        worker = self.worker_factory(settings, keys, self.session_id, mode=mode)
        self.worker = worker
        self.active_mode = mode
        worker.status.connect(self._status)
        worker.log.connect(self._worker_log)
        worker.spoken.connect(self._spoken)
        worker.audio.connect(self._audio)
        worker.demo_speech.connect(self._demo_speech)
        worker.catalogue.connect(self._catalogue)
        worker.finished.connect(self._finished)
        self._set_active(True)
        self.status.setText('●  Preparando demo…' if mode == 'demo' else '●  Preparando…')
        worker.start()

    def _preview_selected(self):
        engine = self.tts_engine.currentData()
        self._start('preview' if engine == 'gemini' else 'preview_local')

    def _tts_changed(self):
        engine = self.tts_engine.currentData()
        local = engine == 'local'
        qwen = engine == 'qwen'
        self.local_voice.setEnabled(local)
        self.qwen_accent.setEnabled(qwen)
        self.qwen_style.setEnabled(qwen)
        self.voice.setEnabled(engine == 'gemini')
        self.language.setEnabled(engine == 'gemini')
        self.gender.setEnabled(engine in ('gemini', 'qwen'))
        self.pitch.setEnabled(engine == 'gemini' or qwen)
        self.rate.setEnabled(engine != 'qwen')
        self.rate.setRange(.75, 1.25) if engine != 'gemini' else self.rate.setRange(.25, 4)
        self.rate.setSingleStep(.05 if engine != 'gemini' else .1)

    def _toggle_microphone(self):
        if self.mic_worker is not None:
            self.mic_worker.stop()
            self.mic_button.setEnabled(False)
            self.mic_status.setText('Apagando micrófono…')
            return
        if self.worker is None:
            self._log('Inicia primero Demo o LIVE para pronunciar los dictados.')
            return
        self.mic_worker = MicWorker(self)
        self.mic_worker.transcript.connect(self._on_transcript)
        self.mic_worker.state.connect(self._mic_state)
        self.mic_worker.speech_active.connect(self._speech_active)
        self.mic_worker.finished.connect(self._mic_finished)
        self.mic_button.setText('■ Apagar micrófono')
        self.mic_status.setText('Iniciando escucha local…')
        self.mic_worker.start()

    def _on_transcript(self, text):
        if self.worker:
            self.worker.inject_dictation(text)
            self._log('Dictado reconocido y enviado a la voz sintética local.')

    def _mic_state(self, text):
        self.mic_status.setText(text)

    def _speech_active(self, active):
        if self.worker:
            self.worker.set_mic_active(active)
        if active:
            self._resume_audio_after_mic = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            if self._resume_audio_after_mic:
                self.player.pause()
        elif getattr(self, '_resume_audio_after_mic', False):
            self.player.play()
            self._resume_audio_after_mic = False

    def _mic_finished(self):
        mic = self.mic_worker
        self.mic_worker = None
        if mic:
            mic.deleteLater()
        self.mic_button.setEnabled(self.worker is not None)
        self.mic_button.setText('🎙 Activar micrófono y repetir con IA')
        if self.worker:
            self.worker.set_mic_active(False)
        if getattr(self, '_resume_audio_after_mic', False):
            self.player.play()
            self._resume_audio_after_mic = False
        if self.closing and self.worker is None:
            QTimer.singleShot(0, self.close)

    def _clear_memory(self):
        try:
            MemoryStore().clear_context()
            self.memory_facts.clear()
            self.activity_context.clear()
            settings = self._settings()
            settings.activity_context = ''
            save_settings(settings, self.settings_path)
            self._log('Memoria local borrada.')
        except OSError:
            self._log('No se pudo borrar la memoria local; comprueba los permisos.')

    def _export_memory(self):
        filename, _filter = QFileDialog.getSaveFileName(self, 'Exportar contexto', 'contexto-directo.json',
                                                       'JSON (*.json)')
        if filename:
            try:
                MemoryStore().export(filename)
                self._log('Contexto exportado.')
            except OSError:
                self._log('No se pudo exportar el contexto.')

    def _set_active(self, active):
        for widget in [self.tabs, self.handle, self.start_button, self.demo_button, self.save_button]:
            widget.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.sim_comment.setEnabled(active and self.active_mode == 'demo')
        self.sim_join.setEnabled(active and self.active_mode == 'demo')
        self.mic_button.setEnabled(active)

    def _stop(self):
        if self.worker is None:
            return
        self.session_id += 1
        self.stop_button.setEnabled(False)
        self.sim_comment.setEnabled(False)
        self.sim_join.setEnabled(False)
        self.status.setText('●  Deteniendo · esperando limpieza…')
        self.player.stop()
        if self.mic_worker:
            self.mic_worker.stop()
        if self.tts:
            self.tts.stop()
        self._remove_audio()
        self.worker.stop()

    def _finished(self):
        worker = self.worker
        self.worker = None
        self.player.stop()
        self._remove_audio()
        self._set_active(False)
        self.status.setText('●  Listo · sesión finalizada')
        if worker:
            worker.deleteLater()
        if self.mic_worker:
            self.mic_worker.stop()
        if self.closing and self.mic_worker is None:
            QTimer.singleShot(0, self.close)

    def _valid(self, session_id):
        return self.worker is not None and session_id == self.session_id

    def _status(self, session_id, text):
        if self._valid(session_id):
            self.status.setText('●  ' + text)

    def _worker_log(self, session_id, text):
        if self._valid(session_id):
            self._log(text)

    def _spoken(self, session_id, text):
        if self._valid(session_id):
            self._log('Voz: ' + text)

    def _log(self, text):
        self.activity.append('') if False else None
        cursor = self.activity.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if not self.activity.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(str(text))
        self.activity.setTextCursor(cursor)
        self.activity.ensureCursorVisible()

    def _volume_changed(self, value):
        if self.output:
            self.output.setVolume(value / 100)
        if self.tts:
            self.tts.setVolume(value / 100)

    def _audio(self, session_id, data):
        if not self._valid(session_id):
            return
        if self.mic_worker:
            self.mic_worker.set_paused(True)
        self._remove_audio()
        try:
            if self.output is None:
                self.output = QAudioOutput(self)
                self.output.setVolume(self.volume.value() / 100)
                self.player.setAudioOutput(self.output)
            with tempfile.NamedTemporaryFile(suffix='.wav', prefix='narrador-', delete=False) as audio_file:
                audio_file.write(data)
                self.audio_path = audio_file.name
            self.player.setSource(QUrl.fromLocalFile(self.audio_path))
            self.player.play()
        except OSError:
            self._log('No se pudo preparar el audio temporal.')
            self._acknowledge(False)

    def _remove_audio(self):
        self.player.setSource(QUrl())
        if self.audio_path:
            try:
                os.unlink(self.audio_path)
            except OSError:
                pass
            self.audio_path = None

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._remove_audio()
            if self.mic_worker:
                self.mic_worker.set_paused(False)
            self._acknowledge(True)

    def _media_error(self, *args):
        self._log('No se pudo reproducir el audio. Comprueba la salida de sonido del sistema.')
        self._remove_audio()
        if self.mic_worker:
            self.mic_worker.set_paused(False)
        self._acknowledge(False)

    def _acknowledge(self, success=True):
        if self.worker and self.stop_button.isEnabled():
            self.worker.acknowledge(success)

    def _demo_speech(self, session_id, text):
        if not self._valid(session_id):
            return
        self._log('Voz: ' + text)
        self._acknowledge()

    def _tts_state(self, state):
        if state == self.tts.State.Error:
            self._log('Demo solo texto: la voz local no está disponible.')
        if state in (self.tts.State.Ready, self.tts.State.Error):
            self._acknowledge()

    def _inject(self, kind):
        if self.worker and self.active_mode == 'demo':
            import uuid
            self.worker.inject(kind, uuid.uuid4().hex, 'María', '¡Hola! ¿Cómo va el directo?')

    def _catalogue(self, session_id, kind, items):
        if not self._valid(session_id):
            return
        if kind == 'voices':
            self.voices = items
            self._filter_voices()
        elif kind in ('models', 'local_models'):
            current = self.model.currentText()
            self.model.clear()
            for item in items:
                self.model.addItem(item if isinstance(item, str) else item.get('name', ''))
            self.model.setCurrentText(current)
            if not items:
                self._log('No se encontraron modelos. Comprueba Ollama y descarga uno, por ejemplo: ollama pull llama3.2')

    def _refresh_models(self):
        self._start('local_models' if self.reply_provider.currentData() == 'ollama' else 'models')

    def _filter_voices(self, *args):
        self.voice.clear()
        self.voice.addItem('Automática según idioma y género', '')
        for voice in self.voices:
            if isinstance(voice, dict):
                name = voice.get('name', '')
                if name:
                    self.voice.addItem(name, name)

    def closeEvent(self, event):
        if self.worker is not None or self.mic_worker is not None:
            self.closing = True
            self._stop()
            if self.mic_worker:
                self.mic_worker.stop()
            event.ignore()
        else:
            self.player.stop()
            if self.tts:
                self.tts.stop()
            self._remove_audio()
            event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description='TikTok Narrador LIVE')
    parser.add_argument('--smoke', nargs='?', const='/tmp/tiktok-narrador-smoke.png', help='Captura offline y salida')
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('TikTok Narrador LIVE')
    window = MainWindow()
    window.show()
    if args.smoke:
        def capture():
            window.grab().save(str(Path(args.smoke).resolve()))
            window.close()
            app.quit()
        QTimer.singleShot(350, capture)
    return app.exec()
