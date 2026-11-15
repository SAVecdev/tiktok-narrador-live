import json

import pytest

from narrador.config import SecretStore, Settings, load_settings, save_settings


def test_roundtrip_and_allowlist(tmp_path):
    path = tmp_path / 'settings.json'
    save_settings(Settings(rate=1.5, remember=True, remember_context=True, activity_context='Minecraft'), path)
    assert load_settings(path) == (Settings(rate=1.5, remember=True, remember_context=True,
                                            activity_context='Minecraft'), None)
    path.write_text(json.dumps({'tts': 'secret', 'rate': 2}))
    loaded, warning = load_settings(path)
    save_settings(loaded, path)
    assert warning is None and 'secret' not in path.read_text()


@pytest.mark.parametrize('data', ['[]', '{', '{"rate": "bad"}', '{"rate": NaN}'])
def test_corrupt_settings_restore_defaults(tmp_path, data):
    path = tmp_path / 'settings.json'
    path.write_text(data)
    assert load_settings(path)[0] == Settings()
    assert load_settings(path)[1]


def test_insecure_backend_is_never_used(monkeypatch):
    class Insecure:
        priority = 10
        def get_password(self, *args):
            pytest.fail('insecure backend accessed')
    monkeypatch.setattr('keyring.get_keyring', lambda: Insecure())
    assert SecretStore().load()[1]
    assert SecretStore().save({'tts': 'secret'})


def test_store_replaces_and_deletes(monkeypatch):
    class Vault:
        priority = 1
        def __init__(self):
            self.values = {'tts': 'old', 'gemini': 'old'}
        def get_password(self, service, provider):
            return self.values.get(provider)
        def set_password(self, service, provider, value):
            self.values[provider] = value
        def delete_password(self, service, provider):
            del self.values[provider]
    Vault.__module__ = 'keyring.backends.SecretService'
    vault = Vault()
    monkeypatch.setattr('keyring.get_keyring', lambda: vault)
    assert SecretStore().save({'tts': 'new'}) is None
    assert vault.values == {'tts': 'new'}
    assert SecretStore().forget() is None
    assert vault.values == {}


def test_failed_deletion_is_reported(monkeypatch):
    class Vault:
        priority = 1
        def get_password(self, *args):
            return 'old'
        def delete_password(self, *args):
            raise RuntimeError('secret should not appear')
    Vault.__module__ = 'keyring.backends.SecretService'
    monkeypatch.setattr('keyring.get_keyring', lambda: Vault())
    message = SecretStore().forget()
    assert 'eliminación' in message and 'secret should' not in message


def test_legacy_activity_is_not_loaded_without_memory_opt_in(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'activity_context': 'private legacy activity'}))
    settings, warning = load_settings(path)
    assert warning is None and settings.activity_context == ''
    assert 'private legacy activity' not in path.read_text()


def test_legacy_fixed_greeting_becomes_ai_greeting_style(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'greeting_template': 'Hola {usuario}, bienvenido al directo.'}))
    settings, warning = load_settings(path)
    assert warning is None
    assert '{usuario}' not in settings.greeting_template
    assert 'natural' in settings.greeting_template
    assert '{usuario}' not in path.read_text()
