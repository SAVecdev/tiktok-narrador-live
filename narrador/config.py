"""Validated non-secret preferences and optional operating-system credential storage."""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import keyring

PROVIDERS = ('tts', 'gemini', 'euler')


@dataclass
class Settings:
    handle: str = ''
    language: str = 'es-US'
    gender: str = 'FEMALE'
    voice: str = ''
    pitch: float = 0.0
    rate: float = 1.0
    volume: float = 0.8
    mode: str = 'read'
    greetings: bool = True
    greeting_template: str = 'Hola {usuario}, bienvenido al directo.'
    blocked_words: str = ''
    reply_provider: str = 'ollama'
    ollama_url: str = 'http://localhost:11434'
    model: str = 'llama3.2'
    persona: str = ('Responde en español de forma breve, extrovertida, cálida y juguetona, con un toque '
                    'ligeramente coqueto y siempre apropiado para un directo.')
    activity_context: str = ''
    remember: bool = False
    remember_context: bool = False
    allow_context_cloud: bool = False
    tts_engine: str = 'local'
    local_voice: str = 'ef_dora'
    mic_enabled: bool = False

    def validate(self) -> Settings:
        import re
        for item in fields(self):
            value = getattr(self, item.name)
            default = item.default
            if isinstance(default, bool):
                valid = type(value) is bool
            elif isinstance(default, str):
                valid = isinstance(value, str) and len(value) <= 4000
            else:
                valid = type(value) in (int, float) and math.isfinite(value)
            if not valid:
                raise ValueError('Configuración inválida: revisa los valores.')
        if (self.mode not in ('ignore', 'read', 'answer')
                or self.gender not in ('FEMALE', 'MALE', 'NEUTRAL')
                or not -20 <= self.pitch <= 20 or not 0.25 <= self.rate <= 4
                or not 0 <= self.volume <= 1
                or not re.fullmatch(r'[a-z]{2,3}-[A-Za-z0-9-]{2,20}', self.language)
                or self.reply_provider not in ('ollama', 'gemini')
                or self.tts_engine not in ('local', 'gemini')
                or self.local_voice not in ('ef_dora', 'ef_sarah', 'ef_nicole', 'ef_kore')
                or not re.fullmatch(r'https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?', self.ollama_url.rstrip('/'))
                or not re.fullmatch(r'[A-Za-z0-9._:-]+', self.model)
                or (self.voice and not re.fullmatch(r'[A-Za-z0-9-]+', self.voice))):
            raise ValueError('Configuración inválida: revisa idioma, voz y rangos.')
        return self


def load_settings(path: str | Path) -> tuple[Settings, str | None]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise TypeError
        allowed = {f.name for f in fields(Settings)}
        settings = Settings(**{k: v for k, v in data.items() if k in allowed}).validate()
        if not settings.remember_context and settings.activity_context:
            settings.activity_context = ''
            try:
                save_settings(settings, path)
            except OSError:
                return settings, 'Se ocultó el contexto antiguo, pero no se pudo borrarlo del archivo de configuración.'
        return settings, None
    except FileNotFoundError:
        return Settings(), None
    except (ValueError, TypeError, OSError):
        return Settings(), 'No se pudo leer la configuración; se usan valores predeterminados.'


def save_settings(settings: Settings, path: str | Path) -> None:
    settings.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.preferences-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SecretStore:
    SERVICE = 'TikTokNarradorLive'
    SECURE_MODULES = frozenset({'keyring.backends.Windows', 'keyring.backends.macOS',
                                'keyring.backends.SecretService', 'keyring.backends.kwallet'})

    def _backend(self):
        backend = keyring.get_keyring()
        if type(backend).__module__ not in self.SECURE_MODULES or backend.priority <= 0:
            raise RuntimeError('No secure vault')
        return backend

    def load(self) -> tuple[dict[str, str], str | None]:
        keys = dict.fromkeys(PROVIDERS, '')
        try:
            backend = self._backend()
            for provider in PROVIDERS:
                keys[provider] = backend.get_password(self.SERVICE, provider) or ''
            return keys, None
        except Exception:  # noqa: BLE001 - vault plugins expose backend-specific exceptions
            return keys, 'No se pudo acceder al almacén seguro; usa claves solo para esta sesión.'

    def save(self, keys: dict[str, str], remember: bool = True) -> str | None:
        try:
            backend = self._backend()
            failed = False
            for provider in PROVIDERS:
                try:
                    value = keys.get(provider, '') if remember else ''
                    if value:
                        backend.set_password(self.SERVICE, provider, value)
                    elif backend.get_password(self.SERVICE, provider) is not None:
                        backend.delete_password(self.SERVICE, provider)
                except Exception:  # noqa: BLE001 - vault plugins expose backend-specific exceptions
                    failed = True
            if failed:
                return ('No se pudieron guardar o eliminar algunas claves del almacén seguro; '
                        'no se confirma su eliminación. Puedes usarlas durante esta sesión.')
            return None
        except Exception:  # noqa: BLE001 - vault plugins expose backend-specific exceptions
            return ('Almacén seguro no disponible; las claves son solo para esta sesión. '
                    'No se pudo confirmar la eliminación de claves guardadas.')

    def forget(self) -> str | None:
        return self.save({}, remember=False)
