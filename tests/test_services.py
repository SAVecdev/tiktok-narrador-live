import asyncio
import json
import wave
from io import BytesIO

import httpx
import pytest

from narrador.config import Settings
from narrador.services import GoogleServices, ProviderError


def run_service(handler, method, *args, settings=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GoogleServices(client, {'tts': 'google-secret', 'gemini': 'google-secret'},
                                     settings or Settings())
            return await getattr(service, method)(*args)
    return asyncio.run(run())


def test_gemini_tts_uses_one_ai_studio_key_and_returns_wav():
    pcm = b'\x00\x00' * 240
    def handler(request):
        assert request.url.host == 'generativelanguage.googleapis.com'
        assert request.url.path == '/v1beta/interactions'
        assert request.headers['x-goog-api-key'] == 'google-secret'
        assert 'secret' not in str(request.url)
        body = json.loads(request.content)
        assert body['response_format']['type'] == 'audio'
        assert body['model'] == 'gemini-3.8-flash-tts'
        assert body['generation_config']['speech_config'][0]['voice'] == 'Kore'
        assert body['input'][0]['content'][0]['text'] == 'hola'
        import base64
        bio = BytesIO()
        with wave.open(bio, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(pcm)
        return httpx.Response(200, json={'steps': [{'content': [{'type': 'audio',
            'data': base64.b64encode(bio.getvalue()).decode()}]}]})
    wav = run_service(handler, 'synthesize', 'hola')
    assert wav.startswith(b'RIFF') and wav[8:12] == b'WAVE'


def test_ollama_reply_is_local_and_does_not_use_google_key():
    def handler(request):
        assert request.url.host == 'localhost' and request.url.path == '/api/chat'
        assert 'x-goog-api-key' not in request.headers
        body = json.loads(request.content)
        assert body['stream'] is False and body['options']['num_predict'] == 80
        assert body['keep_alive'] == '10m'
        return httpx.Response(200, json={'message': {'content': 'Hola, Ana'}})
    assert run_service(handler, 'reply', 'Ana', 'hola') == 'Hola, Ana'


def test_ollama_models_catalogue():
    def handler(request):
        assert request.url == 'http://localhost:11434/api/tags'
        return httpx.Response(200, json={'models': [{'name': 'llama3.2'}]})
    assert run_service(handler, 'local_models') == ['llama3.2']


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500])
def test_gemini_errors_are_sanitized(status):
    with pytest.raises(ProviderError) as error:
        run_service(lambda _: httpx.Response(status, text='google-secret'), 'synthesize', 'hola')
    assert 'secret' not in str(error.value)


def test_gemini_voice_catalogue_is_selectable_without_fake_metadata():
    result = run_service(lambda _: httpx.Response(500), 'voices')
    assert {'name': 'Kore'} in result
    assert all(set(item) == {'name'} for item in result)


def test_activity_context_is_sent_to_local_model():
    def handler(request):
        body = json.loads(request.content)
        assert 'jugando Minecraft' in body['messages'][0]['content']
        return httpx.Response(200, json={'message': {'content': 'Qué construyes?'}})
    assert run_service(handler, 'reply', 'Ana', 'hola',
                       settings=Settings(activity_context='Estoy jugando Minecraft')) == 'Qué construyes?'


def test_local_join_greeting_is_personalized_and_contextual():
    def handler(request):
        assert request.url.host == 'localhost' and request.url.path == '/api/chat'
        body = json.loads(request.content)
        assert body['model'] == 'llama3.2'
        assert body['keep_alive'] == '10m'
        assert body['options']['num_predict'] == 48
        assert 'no uses la fórmula' in body['messages'][0]['content']
        assert 'Minecraft' in body['messages'][0]['content']
        assert 'Ana' in body['messages'][1]['content']
        return httpx.Response(200, json={'message': {'content': '¡Ana, justo estábamos por entrar a la cueva!'}})
    assert run_service(handler, 'greet', 'Ana',
                       settings=Settings(activity_context='Estoy jugando Minecraft')) == (
                           '¡Ana, justo estábamos por entrar a la cueva!')


def test_gemini_reply_includes_activity_context():
    def handler(request):
        body = json.loads(request.content)
        system = body['systemInstruction']['parts'][0]['text']
        assert 'Estoy jugando Minecraft' in system
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'Qué construyes?'}]}}]})
    assert run_service(handler, 'reply', 'Ana', 'hola',
                       settings=Settings(reply_provider='gemini', activity_context='Estoy jugando Minecraft',
                                         allow_context_cloud=True)) == 'Qué construyes?'


def test_gemini_does_not_receive_activity_without_explicit_consent():
    def handler(request):
        body = json.loads(request.content)
        assert 'Contexto del directo' not in body['systemInstruction']['parts'][0]['text']
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'Hola'}]}}]})
    assert run_service(handler, 'reply', 'Ana', 'hola', settings=Settings(
        reply_provider='gemini', activity_context='Estoy jugando Minecraft')) == 'Hola'
