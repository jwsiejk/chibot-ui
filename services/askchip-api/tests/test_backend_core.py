from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.events import EventBus
from app.main import create_app
from app.speech import SpeechService
from app.storage import Database
from app.turns import TurnManager
from app.prompting import PromptAssembler
from app.stt import SttError, SttResult
from app.webrtc.session_store import WebRtcSessionStore

CONTRACT_MESSAGE_KEYS = {
    'id',
    'session_id',
    'role',
    'source',
    'modality',
    'status',
    'text',
    'created_at',
    'committed_at',
    'completed_at',
    'metadata',
}
CONTRACT_TRANSCRIPT_STATES = {'ready', 'listening', 'transcribing', 'thinking', 'speaking', 'error'}
CONTRACT_SOURCES_BY_ROLE = {'assistant': 'model_output'}
CONTRACT_SOURCE_VOCABULARY = {'typed_input', 'voice_input', 'model_output', 'system_notice'}


class FakeSttService:
    def __init__(self, *, text: str = 'voice transcript', error: str | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, object]] = []

    def transcribe_bytes(self, audio_bytes: bytes, *, filename: str | None = None) -> SttResult:
        self.calls.append({'audio_bytes': audio_bytes, 'filename': filename})
        if self.error:
            raise SttError(self.error)
        return SttResult(text=self.text, language='en', duration_seconds=1.2, segments=[{'text': self.text}])


def make_app(tmp_path: Path, transport: httpx.AsyncBaseTransport | None = None, webrtc_peer_factory=None, stt_service=None, tts_adapter=None, **settings_overrides):
    config = Settings(
        database_path=tmp_path / 'askchip.db',
        ollama_warmup_enabled=settings_overrides.pop('ollama_warmup_enabled', False),
        tts_warmup_enabled=settings_overrides.pop('tts_warmup_enabled', False),
        **settings_overrides,
    )
    return create_app(config=config, ollama_transport=transport, webrtc_peer_factory=webrtc_peer_factory, stt_service=stt_service, tts_adapter=tts_adapter)




class FakeTtsService:
    def __init__(self, *, audio_bytes: bytes = b'RIFFfake', error: str | None = None) -> None:
        self.audio_bytes = audio_bytes
        self.error = error
        self.calls: list[str] = []

    def synthesize(self, text: str):
        from app.tts import SynthesizedSpeech, TtsError

        self.calls.append(text)
        if self.error:
            raise TtsError(self.error)
        return SynthesizedSpeech(
            audio_bytes=self.audio_bytes,
            content_type='audio/wav',
            sample_rate_hz=24000,
            duration_ms=100,
            metadata={'engine': 'kokoro', 'voice': 'af_heart'},
        )


class FakeManagedPeer:
    def __init__(self) -> None:
        self.closed = False
        self.terminal_callback = None

    def set_terminal_state_callback(self, callback) -> None:
        self.terminal_callback = callback

    async def emit_terminal_state(self, state: str) -> None:
        if self.terminal_callback is not None:
            await self.terminal_callback(state)

    async def close(self) -> None:
        self.closed = True


class FakePeerFactory:
    def __init__(self, *, status: str = 'answer_created', detail: str = 'ok') -> None:
        self.status = status
        self.detail = detail
        self.calls = 0
        self.peers: list[FakeManagedPeer] = []

    async def create_answer(self, offer) -> object:
        from app.webrtc.peer_factory import PeerFactoryResult
        from app.webrtc_models import SessionDescriptionModel

        self.calls += 1
        if self.status != 'answer_created':
            return PeerFactoryResult(status=self.status, detail=self.detail)
        peer = FakeManagedPeer()
        self.peers.append(peer)
        return PeerFactoryResult(
            status='answer_created',
            detail=self.detail,
            answer=SessionDescriptionModel(type='answer', sdp=f'answer-for:{offer.sdp}'),
            peer=peer,
        )




def make_speech_service(tmp_path: Path, *, tts_adapter: FakeTtsService | None = None):
    db = Database(tmp_path / 'speech-service.db')
    db.initialize()
    event_bus = EventBus()
    turn_manager = TurnManager(db, event_bus, ollama=None, prompt_assembler=PromptAssembler())
    speech = SpeechService(db, event_bus, turn_manager, tts_adapter or FakeTtsService())
    return db, speech


def seed_session_with_assistant_message(db: Database, *, status: str, text: str, detail: str = 'seeded'):
    from app.domain_models import MessageRecord, SessionRecord

    session = SessionRecord(title='Speech test', status='thinking' if status == 'streaming' else 'ready')
    db.create_session(session)
    message = MessageRecord(
        session_id=session.id,
        role='assistant',
        text=text,
        status=status,
        turn_id='turn-1',
        source='model_output',
        modality='text',
        metadata={'detail': detail},
    )
    db.create_message(message)
    return session, message

def streaming_transport(chunks: list[dict[str, object]], status_code: int = 200) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gemma3:4b'}]})
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


def test_faster_whisper_stt_closes_tempfile_before_transcribe_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.stt import FasterWhisperSttService

    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, path: str, vad_filter: bool = True):
            temp_path = Path(path)
            captured['path'] = temp_path
            captured['vad_filter'] = vad_filter
            captured['exists_during_transcribe'] = temp_path.exists()
            captured['bytes'] = temp_path.read_bytes()
            temp_path.rename(temp_path.with_suffix(temp_path.suffix + '.checked'))
            moved_path = temp_path.with_suffix(temp_path.suffix + '.checked')
            moved_path.rename(temp_path)
            return iter([SimpleNamespace(text='hello', start=0.0, end=1.0)]), SimpleNamespace(duration=1.0, language='en')

    monkeypatch.setattr(FasterWhisperSttService, '_load_model', staticmethod(lambda *args: FakeModel()))

    service = FasterWhisperSttService(model_name='tiny', device='cpu', compute_type='int8', cpu_threads=1)
    result = service.transcribe_bytes(b'voice-bytes', filename='voice-turn.webm')

    temp_path = captured['path']
    assert isinstance(temp_path, Path)
    assert captured['vad_filter'] is False
    assert captured['exists_during_transcribe'] is True
    assert captured['bytes'] == b'voice-bytes'
    assert result.text == 'hello'
    assert not temp_path.exists()




def test_default_settings_use_gemma_model() -> None:
    config = Settings()

    assert config.ollama_model == 'gemma3:4b'
    assert config.ollama_num_parallel == 1


def test_config_endpoint_reports_default_model(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get('/api/v1/config')

    assert response.status_code == 200
    assert response.json()['ollama_model'] == 'gemma3:4b'
    assert response.json()['ollama_keep_alive'] == '30m'
    assert response.json()['ollama_num_ctx'] == 8192
    assert response.json()['ollama_num_parallel'] == 1
    assert response.json()['stt_requested_device'] == 'auto'
    assert response.json()['stt_requested_compute_type'] == 'auto'


def test_local_readme_documents_ollama_num_parallel_pin() -> None:
    readme_text = (Path(__file__).resolve().parents[3] / 'docs/askchip-local/README.md').read_text(encoding='utf-8')

    assert 'OLLAMA_NUM_PARALLEL=1' in readme_text
    assert 'Ollama memory use scales' in readme_text


def test_turns_send_explicit_think_false_for_small_talk(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Hey there!'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Think false'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'hey'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    assert payload['think'] is False
    assert payload['keep_alive'] == '30m'
    assert payload['options']['num_ctx'] == 8192
    reasoning_events = [event for event in transcript['events'] if event['type'] == 'reasoning.selected']
    assert reasoning_events[-1]['payload'] == {'mode': 'default', 'think': False}


def test_turns_keep_think_false_even_for_complex_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        body = json.dumps({'message': {'content': 'Start with logs and check stack traces.'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Auto think'}).json()['id']
        text = 'Help me debug this crash and compare tradeoffs between two fixes.'
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': text})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    assert payload['think'] is False
    reasoning_events = [event for event in transcript['events'] if event['type'] == 'reasoning.selected']
    assert reasoning_events[-1]['payload'] == {'mode': 'default', 'think': False}


def test_turns_do_not_inject_qwen_no_think_control_message(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Hey there!'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), ollama_model='gemma3:4b')
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'No think control'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'hey'})

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    contents = [message['content'] for message in payload['messages']]
    assert '/no_think' not in contents


def test_turns_do_not_inject_qwen_think_control_message(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Start with logs.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), ollama_model='gemma3:4b')
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Think control'}).json()['id']
        turn = client.post(
            f'/api/v1/sessions/{session_id}/turns',
            json={'text': 'Help me debug this crash and compare tradeoffs between two fixes.'},
        )

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    contents = [message['content'] for message in payload['messages']]
    assert '/think' not in contents


def test_manual_think_token_is_treated_as_plain_text(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        body = json.dumps({'message': {'content': 'Here is the deeper breakdown.'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Forced think'}).json()['id']
        text = '/think Walk me through this in detail'
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': text})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    assert payload['think'] is False
    user_message = transcript['messages'][0]
    assert user_message['role'] == 'user'
    assert user_message['text'] == '/think Walk me through this in detail'
    reasoning_events = [event for event in transcript['events'] if event['type'] == 'reasoning.selected']
    assert reasoning_events[-1]['payload'] == {'mode': 'default', 'think': False}


def test_think_only_turn_is_treated_as_plain_text(tmp_path: Path) -> None:
    app = make_app(tmp_path, transport=streaming_transport([{'message': {'content': 'unused'}, 'done': True}]))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Think only'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': '/think'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    assert transcript['messages'][0]['text'] == '/think'


def test_deep_only_turn_is_treated_as_plain_text(tmp_path: Path) -> None:
    app = make_app(tmp_path, transport=streaming_transport([{'message': {'content': 'unused'}, 'done': True}]))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Deep only'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': '/deep'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    assert transcript['messages'][0]['text'] == '/deep'


def test_ollama_thinking_field_is_never_persisted_in_canonical_text(tmp_path: Path) -> None:
    transport = streaming_transport([
        {'message': {'thinking': 'private chain of thought', 'content': ''}, 'done': False},
        {'message': {'thinking': 'still private', 'content': 'Final answer'}, 'done': True},
    ])
    app = make_app(tmp_path, transport=transport)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'No cot leak'}).json()['id']
        response = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'solve this carefully'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 201
    assistant = transcript['messages'][-1]
    assert assistant['role'] == 'assistant'
    assert assistant['text'] == 'Final answer'
    assert 'private chain of thought' not in assistant['text']
    assert assistant['metadata']['thinking_present'] is True

def test_think_block_leak_in_content_is_filtered_from_deltas_transcript_and_final_text(tmp_path: Path) -> None:
    transport = streaming_transport([
        {'message': {'content': '<think>I should not leak this'}, 'done': False},
        {'message': {'content': ' private reasoning</think>Final answer begins. '}, 'done': False},
        {'message': {'content': 'And continues.'}, 'done': True},
    ])
    app = make_app(tmp_path, transport=transport)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Filter think block'}).json()['id']
        response = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'answer carefully'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 201
    assistant = transcript['messages'][-1]
    assert assistant['role'] == 'assistant'
    assert assistant['text'] == 'Final answer begins. And continues.'
    assert 'reasoning' not in assistant['text'].lower()
    assert assistant['metadata']['thinking_leak_filtered'] is True
    delta_payloads = [event['payload']['delta'] for event in transcript['events'] if event['type'] == 'assistant.delta']
    assert all('think' not in chunk.lower() for chunk in delta_payloads)
    assert all('reasoning' not in chunk.lower() for chunk in delta_payloads)


def test_session_creation_and_listing(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'My chat'})
        listed = client.get('/api/v1/sessions')

    assert created.status_code == 201
    assert listed.status_code == 200
    items = listed.json()['items']
    assert len(items) == 1
    assert items[0]['title'] == 'My chat'
    assert items[0]['status'] == 'ready'
    assert items[0]['ready_at'] is not None


def test_delete_session_removes_only_target_session_data(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        first_session_id = client.post('/api/v1/sessions', json={'title': 'Delete me'}).json()['id']
        second_session_id = client.post('/api/v1/sessions', json={'title': 'Keep me'}).json()['id']
        client.post(f'/api/v1/sessions/{first_session_id}/turns', json={'text': 'first question'})
        client.post(f'/api/v1/sessions/{second_session_id}/turns', json={'text': 'second question'})

        deleted = client.delete(f'/api/v1/sessions/{first_session_id}')
        listed = client.get('/api/v1/sessions')
        deleted_transcript = client.get(f'/api/v1/sessions/{first_session_id}/transcript')
        kept_transcript = client.get(f'/api/v1/sessions/{second_session_id}/transcript')

    assert deleted.status_code == 200
    assert deleted.json() == {'status': 'deleted', 'session_id': first_session_id}
    assert [item['id'] for item in listed.json()['items']] == [second_session_id]
    assert deleted_transcript.status_code == 404
    assert kept_transcript.status_code == 200
    assert kept_transcript.json()['session']['id'] == second_session_id
    assert [message['text'] for message in kept_transcript.json()['messages']] == ['second question', 'Reply']


def test_typed_turn_commits_and_assembles_assistant_message(tmp_path: Path) -> None:
    transport = streaming_transport(
        [
            {'message': {'content': 'Hello'}, 'done': False},
            {'message': {'content': ' world'}, 'done': False, 'eval_count': 12},
            {'message': {'content': '!'}, 'done': True, 'total_duration': 99},
        ]
    )
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Chat'}).json()['id']
        response = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert response.status_code == 201
    data = transcript.json()
    assert [message['role'] for message in data['messages']] == ['user', 'assistant']
    assert data['messages'][0]['text'] == 'Hi'
    assert data['messages'][0]['source'] == 'typed_input'
    assert data['messages'][0]['modality'] == 'text'
    assert data['messages'][0]['committed_at'] is not None
    assert data['messages'][1]['text'] == 'Hello world!'
    assert data['messages'][1]['status'] == 'completed'
    assert data['messages'][1]['source'] == 'model_output'
    assert data['messages'][1]['completed_at'] is not None
    assert 'provider_metrics' in data['messages'][1]['metadata']
    assert data['session']['status'] == 'ready'
    event_types = [event['type'] for event in data['events']]
    assert 'turn.committed' in event_types
    assert 'prompt.assembled' in event_types
    assert 'assistant.started' in event_types
    assert event_types.count('assistant.delta') == 3
    assert 'assistant.completed' in event_types
    state_events = [event for event in data['events'] if event['type'] == 'state']
    assert [event['payload']['state'] for event in state_events[-2:]] == ['thinking', 'ready']
    timings = {timing['phase']: timing for timing in data['timings']}
    assert timings['model_stream']['meta']['first_chunk_ms'] is not None
    assert timings['model_stream']['meta']['total_duration'] == 99


def test_transcript_message_contract_shape_is_locked(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Hello'}, 'done': True}])
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Contract'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert transcript.status_code == 200
    messages = transcript.json()['messages']
    assert len(messages) == 2
    for message in messages:
        assert set(message) == CONTRACT_MESSAGE_KEYS
        assert 'content' not in message
        assert 'turn_id' not in message
        assert 'updated_at' not in message


def test_transcript_role_and_source_semantics_are_distinct(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Semantics'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Question'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert transcript.status_code == 200
    for message in transcript.json()['messages']:
        assert message['source'] in CONTRACT_SOURCE_VOCABULARY
        if message['role'] == 'assistant':
            assert message['source'] == CONTRACT_SOURCES_BY_ROLE[message['role']]
        else:
            assert message['source'] in {'typed_input', 'voice_input'}
        assert message['source'] != message['role']


def test_transcript_only_emits_canonical_source_values(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Vocabulary'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Question'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert transcript.status_code == 200
    sources = {message['source'] for message in transcript.json()['messages']}
    assert sources
    assert sources.issubset(CONTRACT_SOURCE_VOCABULARY)


def test_only_contract_states_are_emitted(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'States'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Question'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert transcript.status_code == 200
    state_events = [event['payload']['state'] for event in transcript.json()['events'] if event['type'] == 'state']
    assert state_events
    assert set(state_events).issubset(CONTRACT_TRANSCRIPT_STATES)


def test_one_active_assistant_job_enforced(tmp_path: Path) -> None:
    start = threading.Event()
    finish = threading.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        start.set()
        await asyncio.to_thread(finish.wait, 2)
        body = json.dumps({'message': {'content': 'done'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    app = make_app(tmp_path, transport=transport)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Concurrent'}).json()['id']
        results: dict[str, object] = {}

        def first_turn() -> None:
            results['first'] = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'One'})

        thread = threading.Thread(target=first_turn)
        thread.start()
        assert start.wait(timeout=2)
        second = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Two'})
        finish.set()
        thread.join(timeout=2)

        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert second.status_code == 409
    assert transcript.status_code == 200
    events = transcript.json()['events']
    assert any(event['type'] == 'error' and event['payload']['code'] == 'assistant_busy' for event in events)


def test_event_persistence_and_websocket_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'WS'}).json()['id']
        with client.websocket_connect(f'/ws/events?session_id={session_id}') as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert first['type'] == 'connection.lifecycle'
    assert second['type'] == 'state'
    assert second['payload']['state'] == 'ready'
    event_types = [event['type'] for event in transcript.json()['events']]
    assert event_types[0] == 'connection.lifecycle'
    assert 'state' in event_types


def test_ollama_unavailable_path_sets_error_state(tmp_path: Path) -> None:
    transport = streaming_transport([], status_code=503)
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Errors'}).json()['id']
        response = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert response.status_code == 503
    data = transcript.json()
    events = data['events']
    assert any(event['type'] == 'error' and event['payload']['code'] == 'ollama_unavailable' for event in events)
    assert any(event['type'] == 'state' and event['payload']['state'] == 'error' for event in events)
    assert data['messages'][-1]['status'] == 'error'
    assert data['session']['status'] == 'error'


def test_prompt_assembler_adds_persona_and_recent_window() -> None:
    assembler = PromptAssembler(transcript_window=2)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='old 1', turn_id='t1', source='typed_input'),
        MessageRecord(session_id='s', role='assistant', text='old 2', turn_id='t1', source='model_output'),
        MessageRecord(session_id='s', role='user', text='recent', turn_id='t2', source='typed_input'),
    ]
    messages = assembler.build_messages(transcript, user_text='new question')

    assert messages[0].role == 'user'
    assert 'You are Marlene inside AskChip Local, a woman' in messages[0].text
    assert 'refer to yourself with she/her pronouns' in messages[0].text
    assert 'Never describe yourself as a man, male, guy, or with any other male self-reference' in messages[0].text
    assert 'middle-aged Nebraska farmer turned tech geek' in messages[0].text
    assert 'Keep answers direct and shorter by default' in messages[0].text
    assert 'Write like you are speaking out loud to one person' in messages[0].text
    assert 'Prefer contractions when natural' in messages[0].text
    assert 'Prefer short, connected sentences over list-like phrasing unless the user asks for a list' in messages[0].text
    assert 'Avoid markdown emphasis, decorative formatting, headings, and bullet formatting unless requested' in messages[0].text
    assert 'Keep the tone human and conversational, not performative' in messages[0].text
    assert 'Be helpful first and personality second' in messages[0].text
    assert 'Do not reveal private/internal reasoning' in messages[0].text
    assert messages[-2].text == 'recent'
    assert messages[-1].role == 'user'
    assert messages[-1].text == 'new question'


def test_prompt_assembler_keeps_canonical_transcript_user_text_plain() -> None:
    assembler = PromptAssembler(transcript_window=4)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='plain prior text', turn_id='t1', source='typed_input'),
        MessageRecord(session_id='s', role='assistant', text='answer', turn_id='t1', source='model_output'),
    ]
    messages = assembler.build_messages(transcript, user_text='fresh plain input')

    user_entries = [message.text for message in messages if message.role == 'user']
    assert user_entries == [messages[0].text, 'plain prior text', 'fresh plain input']


def test_thinking_filter_strips_unmatched_close_tag() -> None:
    from app.thinking_filter import ThinkingLeakFilter

    filterer = ThinkingLeakFilter()
    first = filterer.filter_delta('Final answer starts </think>', done=False)
    second = filterer.filter_delta(' and finishes cleanly.', done=True)

    assert first == 'Final answer starts '
    assert second == ' and finishes cleanly.'
    assert filterer.leak_filtered is True


def test_stt_auto_compute_type_prefers_cuda_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.stt import FasterWhisperSttService

    class FakeCtranslate2:
        @staticmethod
        def get_cuda_device_count() -> int:
            return 1

    monkeypatch.setitem(sys.modules, 'ctranslate2', FakeCtranslate2())
    service = FasterWhisperSttService(model_name='base', device='auto', compute_type='auto', cpu_threads=2)
    runtime = service.runtime_details()

    assert runtime['selected_device'] == 'cuda'
    assert runtime['resolved_compute_type'] == 'int8_float16'


def test_stt_auto_compute_type_uses_cpu_profile_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.stt import FasterWhisperSttService

    class FakeCtranslate2:
        @staticmethod
        def get_cuda_device_count() -> int:
            return 0

    monkeypatch.setitem(sys.modules, 'ctranslate2', FakeCtranslate2())
    service = FasterWhisperSttService(model_name='base', device='auto', compute_type='auto', cpu_threads=2)
    runtime = service.runtime_details()

    assert runtime['selected_device'] == 'cpu'
    assert runtime['resolved_compute_type'] == 'int8'


def test_stt_auto_runtime_no_longer_depends_on_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.stt import FasterWhisperSttService

    class FakeCtranslate2:
        @staticmethod
        def get_cuda_device_count() -> int:
            return 1

    monkeypatch.delitem(sys.modules, 'torch', raising=False)
    monkeypatch.setitem(sys.modules, 'ctranslate2', FakeCtranslate2())
    service = FasterWhisperSttService(model_name='base', device='auto', compute_type='auto', cpu_threads=2)
    runtime = service.runtime_details()

    assert runtime['selected_device'] == 'cuda'
    assert 'warning' not in runtime


def test_tts_auto_mode_selects_cuda_provider_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tts import KokoroConfig, KokoroTtsAdapter

    monkeypatch.setattr('app.tts._available_onnx_providers', lambda: ['CUDAExecutionProvider', 'CPUExecutionProvider'])
    runtime = KokoroTtsAdapter(KokoroConfig(voice='af_heart', model_path=None, voices_path=None, device='auto')).runtime_details()
    assert runtime['selected_device'] == 'cuda'
    assert runtime['provider'] == 'CUDAExecutionProvider'


def test_tts_auto_mode_falls_back_to_cpu_without_cuda_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tts import KokoroConfig, KokoroTtsAdapter

    monkeypatch.setattr('app.tts._available_onnx_providers', lambda: ['CPUExecutionProvider'])
    runtime = KokoroTtsAdapter(KokoroConfig(voice='af_heart', model_path=None, voices_path=None, device='auto')).runtime_details()
    assert runtime['selected_device'] == 'cpu'
    assert runtime['provider'] == 'CPUExecutionProvider'


def test_speech_start_and_stop_merge_metadata_without_replacing_canonical_fields(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Hello'}, 'done': True, 'eval_count': 12}])
    app = make_app(tmp_path, transport=transport, tts_adapter=FakeTtsService())

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speech merge'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        assistant_message_id = turn.json()['assistant_message_id']

        started = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech/start')
        stopped = client.post(
            f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech/stop',
            json={'reason': 'ended'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert started.status_code == 200
    assert stopped.status_code == 200
    assistant_messages = [message for message in transcript.json()['messages'] if message['role'] == 'assistant']
    assert len(assistant_messages) == 1
    metadata = assistant_messages[0]['metadata']
    assert metadata['model']
    assert metadata['provider_metrics']['eval_count'] == 12
    assert metadata['first_chunk_ms'] is not None
    assert metadata['speech']['last_started_at'] is not None
    assert metadata['speech']['last_stopped_at'] is not None
    assert metadata['speech']['stop_reason'] == 'ended'


def test_duplicate_and_stale_speech_stop_do_not_corrupt_session_state(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, tts_adapter=FakeTtsService())

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speech stop'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        assistant_message_id = turn.json()['assistant_message_id']

        assert client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech/start').status_code == 200
        first_stop = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech/stop', json={'reason': 'typed_submit'})
        second_stop = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech/stop', json={'reason': 'typed_submit'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert first_stop.status_code == 200
    assert second_stop.status_code == 200
    data = transcript.json()
    assert data['session']['status'] == 'ready'
    stop_events = [event for event in data['events'] if event['type'] == 'tts.stopped']
    assert len(stop_events) == 1
    assert stop_events[0]['payload']['reason'] == 'typed_submit'


def test_tts_failure_preserves_completed_assistant_text_and_keeps_typed_and_voice_turns_working(tmp_path: Path) -> None:
    responses = iter([
        [{'message': {'content': 'typed reply'}, 'done': True}],
        [{'message': {'content': 'voice reply'}, 'done': True}],
    ])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gemma3:4b'}]})
        chunks = next(responses)
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), stt_service=FakeSttService(text='voice transcript'), tts_adapter=FakeTtsService(error='kokoro missing'))

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'TTS fail'}).json()['id']
        typed = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed question'})
        assistant_message_id = typed.json()['assistant_message_id']
        speech = client.get(f'/api/v1/sessions/{session_id}/messages/{assistant_message_id}/speech')
        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        voice = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert typed.status_code == 201
    assert speech.status_code == 503
    assert started.status_code == 200
    assert voice.status_code == 201
    data = transcript.json()
    assert data['messages'][1]['text'] == 'typed reply'
    assert data['messages'][1]['status'] == 'completed'
    assert data['messages'][3]['text'] == 'voice reply'
    assert data['messages'][3]['status'] == 'completed'
    assert all('content' not in message for message in data['messages'])
    state_events = [event['payload']['state'] for event in data['events'] if event['type'] == 'state']
    assert set(state_events).issubset(CONTRACT_TRANSCRIPT_STATES)

def test_webrtc_offer_route_is_separate_from_typed_chat_contract(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            '/api/v1/webrtc/offer',
            json={'offer': {'type': 'offer', 'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n'}},
        )
        session_id = response.json()['session_id']
        created = client.post('/api/v1/sessions', json={'title': 'Typed chat still works'})
        listed = client.get('/api/v1/sessions')

    assert response.status_code == 200
    assert response.json()['status'] == 'unsupported'
    assert response.json()['answer'] is None
    assert session_id
    assert created.status_code == 201
    assert listed.status_code == 200


def test_webrtc_websocket_offer_keeps_backend_peer_alive_after_socket_close(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-keepalive',
                'offer': {'type': 'offer', 'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n'},
            })
            response = websocket.receive_json()

        session = client.app.state.askchip.webrtc_signaling.get_session('rtc-session-keepalive')
        assert response['status'] == 'answer_created'
        assert response['session_id'] == 'rtc-session-keepalive'
        assert session is not None
        assert session.peer is peer_factory.peers[0]
        assert peer_factory.peers[0].closed is False


def test_webrtc_store_prunes_idle_negotiated_peer_sessions(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    store = WebRtcSessionStore(negotiated_session_timeout_seconds=5)
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        signaling = client.app.state.askchip.webrtc_signaling
        signaling._store = store
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-stale-peer',
                'offer': {'type': 'offer', 'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n'},
            })
            response = websocket.receive_json()

        session = signaling.get_session('rtc-session-stale-peer')
        assert response['status'] == 'answer_created'
        assert session is not None
        assert session.peer is peer_factory.peers[0]
        assert peer_factory.peers[0].closed is False

        session.updated_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({'event': 'disconnect', 'session_id': 'different-session'})
            disconnect_response = websocket.receive_json()

    assert disconnect_response['status'] == 'disconnected'
    assert peer_factory.peers[0].closed is True
    assert signaling.get_session('rtc-session-stale-peer') is None

def test_webrtc_websocket_disconnect_releases_backend_peer(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-disconnect',
                'offer': {'type': 'offer', 'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n'},
            })
            offer_response = websocket.receive_json()

        session = client.app.state.askchip.webrtc_signaling.get_session('rtc-session-disconnect')
        assert session is not None
        assert session.peer is peer_factory.peers[0]
        assert peer_factory.peers[0].closed is False

        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({'event': 'disconnect', 'session_id': 'rtc-session-disconnect'})
            disconnect_response = websocket.receive_json()

        released_session = client.app.state.askchip.webrtc_signaling.get_session('rtc-session-disconnect')
        assert offer_response['status'] == 'answer_created'
        assert disconnect_response == {
            'session_id': 'rtc-session-disconnect',
            'event': 'disconnected',
            'status': 'disconnected',
            'detail': 'WebRTC foundation session released.',
            'answer': None,
        }
        assert released_session is None
        assert peer_factory.peers[0].closed is True


def test_webrtc_offer_reuses_explicit_session_identifier(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            '/api/v1/webrtc/offer',
            json={
                'session_id': 'rtc-session-1',
                'offer': {'type': 'offer', 'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n'},
            },
        )

    assert response.status_code == 200
    assert response.json()['session_id'] == 'rtc-session-1'


def test_load_settings_reads_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv('ASKCHIP_API_HOST', '0.0.0.0')
    monkeypatch.setenv('ASKCHIP_API_PORT', '9000')
    monkeypatch.setenv('ASKCHIP_API_DATABASE_PATH', '/tmp/askchip.db')
    monkeypatch.setenv('ASKCHIP_PROMPT_TRANSCRIPT_WINDOW', '4')
    monkeypatch.setenv('OLLAMA_MODEL', 'custom:model')
    monkeypatch.setenv('OLLAMA_NUM_PARALLEL', '1')

    config = load_settings()

    assert config.host == '0.0.0.0'
    assert config.port == 9000
    assert config.database_path == Path('/tmp/askchip.db')
    assert config.prompt_transcript_window == 4
    assert config.ollama_model == 'custom:model'
    assert config.ollama_num_parallel == 1


def test_startup_migrates_legacy_message_content_column_to_text(tmp_path: Path) -> None:
    db_path = tmp_path / 'askchip.db'
    conn = sqlite3.connect(db_path)
    conn.executescript(
        '''
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'typed_input',
            modality TEXT NOT NULL DEFAULT 'text',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            committed_at TEXT,
            completed_at TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO messages(
            id, session_id, role, content, status, turn_id, source, modality, created_at, updated_at, metadata
        ) VALUES(
            'm1', 's1', 'user', 'legacy text', 'completed', 't1', 'typed_input', 'text',
            '2026-03-19T00:00:00+00:00', '2026-03-19T00:00:00+00:00', '{}'
        );
        '''
    )
    conn.commit()
    conn.close()

    app = make_app(tmp_path)
    with TestClient(app):
        pass

    migrated = sqlite3.connect(db_path)
    migrated.row_factory = sqlite3.Row
    columns = {row['name'] for row in migrated.execute('PRAGMA table_info(messages)').fetchall()}
    row = migrated.execute('SELECT text FROM messages WHERE id = ?', ('m1',)).fetchone()
    migrated.close()

    assert 'text' in columns
    assert 'content' not in columns
    assert row is not None
    assert row['text'] == 'legacy text'


def test_webrtc_websocket_offer_returns_real_answer_when_peer_factory_supports_it(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory(detail='foundation answer created')
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'offer': {'type': 'offer', 'sdp': 'offer-sdp'},
            })
            response = websocket.receive_json()

    assert response['event'] == 'answer'
    assert response['status'] == 'answer_created'
    assert response['answer'] == {'type': 'answer', 'sdp': 'answer-for:offer-sdp'}
    assert peer_factory.calls == 1


def test_webrtc_websocket_disconnect_releases_peer_session(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-1',
                'offer': {'type': 'offer', 'sdp': 'offer-sdp'},
            })
            websocket.receive_json()
            websocket.send_json({'event': 'disconnect', 'session_id': 'rtc-session-1'})
            disconnected = websocket.receive_json()

    assert disconnected['event'] == 'disconnected'
    assert disconnected['status'] == 'disconnected'
    assert peer_factory.peers[0].closed is True


def test_webrtc_websocket_reports_explicit_unsupported_error_without_touching_typed_chat(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory(status='unsupported', detail='aiortc missing in runtime')
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'offer': {'type': 'offer', 'sdp': 'offer-sdp'},
            })
            response = websocket.receive_json()
        created = client.post('/api/v1/sessions', json={'title': 'Typed chat still works'})
        listed = client.get('/api/v1/sessions')

    assert response['status'] == 'unsupported'
    assert response['answer'] is None
    assert response['detail'] == 'aiortc missing in runtime'
    assert created.status_code == 201
    assert listed.status_code == 200


def test_http_webrtc_offer_route_is_compatibility_only(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory(detail='compatibility answer')
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        response = client.post(
            '/api/v1/webrtc/offer',
            json={'offer': {'type': 'offer', 'sdp': 'compat-offer'}},
        )

    assert response.status_code == 200
    assert response.headers['x-askchip-webrtc-compatibility'] == 'http-offer'
    assert response.json()['status'] == 'answer_created'


def test_webrtc_disconnect_is_idempotent_and_clears_session_store(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-cleanup',
                'offer': {'type': 'offer', 'sdp': 'offer-sdp'},
            })
            websocket.receive_json()
        assert client.app.state.askchip.webrtc_signaling.session_count() == 1

        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({'event': 'disconnect', 'session_id': 'rtc-session-cleanup'})
            first = websocket.receive_json()
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({'event': 'disconnect', 'session_id': 'rtc-session-cleanup'})
            second = websocket.receive_json()

        assert first['status'] == 'disconnected'
        assert second['status'] == 'disconnected'
        assert client.app.state.askchip.webrtc_signaling.get_session('rtc-session-cleanup') is None
        assert client.app.state.askchip.webrtc_signaling.session_count() == 0
        assert peer_factory.peers[0].closed is True


def test_webrtc_failed_peer_cleanup_releases_orphaned_session(tmp_path: Path) -> None:
    peer_factory = FakePeerFactory()
    app = make_app(tmp_path, webrtc_peer_factory=peer_factory)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/webrtc') as websocket:
            websocket.send_json({
                'event': 'offer',
                'session_id': 'rtc-session-failed',
                'offer': {'type': 'offer', 'sdp': 'offer-sdp'},
            })
            websocket.receive_json()

        assert client.app.state.askchip.webrtc_signaling.get_session('rtc-session-failed') is not None
        asyncio.run(peer_factory.peers[0].emit_terminal_state('failed'))

        assert client.app.state.askchip.webrtc_signaling.get_session('rtc-session-failed') is None
        assert client.app.state.askchip.webrtc_signaling.session_count() == 0
        assert peer_factory.peers[0].closed is True


def test_session_store_prunes_stale_pending_webrtc_sessions() -> None:
    store = WebRtcSessionStore(pending_session_timeout_seconds=5)
    session = store.resolve_session('stale-pending')
    session.updated_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

    asyncio.run(store.prune_expired_pending_sessions())

    assert store.get('stale-pending') is None
    assert store.size() == 0


def test_voice_turn_commits_only_after_ptt_release(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Voice reply'}, 'done': True}])
    stt = FakeSttService(text='voice hello')
    app = make_app(tmp_path, transport=transport, stt_service=stt)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Voice'}).json()['id']
        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start', headers={'X-AskChip-Device-Id': 'mic-7'})
        transcript_after_start = client.get(f'/api/v1/sessions/{session_id}/transcript')
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm', 'X-AskChip-Device-Id': 'mic-7', 'X-AskChip-Duration-Ms': '321'},
        )
        transcript_after_release = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert started.status_code == 200
    assert transcript_after_start.status_code == 200
    assert transcript_after_start.json()['messages'] == []
    assert released.status_code == 201
    data = transcript_after_release.json()
    assert [message['role'] for message in data['messages']] == ['user', 'assistant']
    assert data['messages'][0]['text'] == 'voice hello'
    assert data['messages'][0]['source'] == 'voice_input'
    assert data['messages'][0]['modality'] == 'voice'
    assert data['messages'][0]['committed_at'] is not None
    event_types = [event['type'] for event in data['events']]
    assert 'ptt.started' in event_types
    assert 'ptt.stopped' in event_types
    assert 'stt.final' in event_types
    assert 'turn.committed' in event_types
    state_events = [event['payload']['state'] for event in data['events'] if event['type'] == 'state']
    assert {'listening', 'transcribing', 'thinking', 'ready'}.issubset(set(state_events))
    assert stt.calls[0]['audio_bytes'] == b'voice-bytes'


def test_typed_and_voice_turns_share_one_canonical_transcript(tmp_path: Path) -> None:
    transport = streaming_transport([
        {'message': {'content': 'typed reply'}, 'done': True},
        {'message': {'content': 'voice reply'}, 'done': True},
    ])
    app = make_app(tmp_path, transport=transport, stt_service=FakeSttService(text='spoken question'))

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Mixed'}).json()['id']
        typed = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed question'})
        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        voice = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert typed.status_code == 201
    assert started.status_code == 200
    assert voice.status_code == 201
    data = transcript.json()
    assert [message['source'] for message in data['messages']] == ['typed_input', 'model_output', 'voice_input', 'model_output']
    assert [message['modality'] for message in data['messages']] == ['text', 'text', 'voice', 'text']


def test_voice_turn_stt_failure_sets_error_state_without_committing_user_message(tmp_path: Path) -> None:
    app = make_app(tmp_path, stt_service=FakeSttService(error='missing faster-whisper runtime'))

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Voice fail'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert released.status_code == 503
    data = transcript.json()
    assert data['messages'] == []
    assert any(event['type'] == 'error' and event['payload']['code'] == 'stt_failed' for event in data['events'])
    assert data['session']['status'] == 'error'


def test_blank_stt_result_does_not_create_blank_canonical_message(tmp_path: Path) -> None:
    app = make_app(tmp_path, stt_service=FakeSttService(text='   '))

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Blank'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert released.status_code == 422
    data = transcript.json()
    assert data['messages'] == []
    assert any(event['type'] == 'error' and event['payload']['code'] == 'stt_empty_transcript' for event in data['events'])


def test_voice_turn_with_think_token_transcript_is_treated_as_plain_text(tmp_path: Path) -> None:
    app = make_app(
        tmp_path,
        stt_service=FakeSttService(text='/think'),
        transport=streaming_transport([{'message': {'content': 'Voice reply'}, 'done': True}]),
    )

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Voice override only'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert released.status_code == 201
    assert transcript['messages'][0]['text'] == '/think'
    assert transcript['messages'][1]['text'] == 'Voice reply'



def test_voice_start_is_rejected_while_assistant_is_busy(tmp_path: Path) -> None:
    start = threading.Event()
    finish = threading.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        start.set()
        await asyncio.to_thread(finish.wait, 2)
        body = json.dumps({'message': {'content': 'done'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), stt_service=FakeSttService(text='voice hello'))

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Busy voice start'}).json()['id']
        thread = threading.Thread(target=lambda: client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed first'}))
        thread.start()
        assert start.wait(timeout=2)
        response = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        finish.set()
        thread.join(timeout=2)
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert response.status_code == 409
    assert any(event['type'] == 'error' and event['payload']['code'] == 'assistant_busy' for event in transcript.json()['events'])


def test_stale_voice_release_is_rejected_without_running_stt(tmp_path: Path) -> None:
    stt = FakeSttService(text='should not run')
    app = make_app(tmp_path, stt_service=stt)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Stale release'}).json()['id']
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert released.status_code == 409
    assert released.json()['detail'] == 'push-to-talk release does not match an active capture'
    assert stt.calls == []
    assert transcript.json()['session']['status'] == 'ready'
    assert transcript.json()['messages'] == []


def test_busy_voice_release_does_not_run_stt_or_leave_session_stuck(tmp_path: Path) -> None:
    start = threading.Event()
    finish = threading.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        start.set()
        await asyncio.to_thread(finish.wait, 2)
        body = json.dumps({'message': {'content': 'done'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    stt = FakeSttService(text='should not run')
    app = make_app(tmp_path, transport=httpx.MockTransport(handler), stt_service=stt)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Busy release'}).json()['id']
        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        assert started.status_code == 200
        thread = threading.Thread(target=lambda: client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed wins'}))
        thread.start()
        assert start.wait(timeout=2)
        released = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm'},
        )
        finish.set()
        thread.join(timeout=2)
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert released.status_code == 409
    assert stt.calls == []
    data = transcript.json()
    assert data['session']['status'] == 'ready'
    state_events = [event['payload']['state'] for event in data['events'] if event['type'] == 'state']
    assert 'listening' in state_events
    assert 'thinking' in state_events
    assert 'transcribing' not in state_events
    assert [message['source'] for message in data['messages']] == ['typed_input', 'model_output']


def test_voice_cancel_invalidates_active_ptt_without_committing_or_running_stt(tmp_path: Path) -> None:
    stt = FakeSttService(text='should not run')
    app = make_app(tmp_path, stt_service=stt)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Cancel'}).json()['id']
        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start', headers={'X-AskChip-Device-Id': 'mic-3'})
        canceled = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/cancel')
        stale_release = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'Content-Type': 'audio/webm', 'X-AskChip-Device-Id': 'mic-3'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert started.status_code == 200
    assert canceled.status_code == 200
    assert stale_release.status_code == 409
    assert stale_release.json()['detail'] == 'push-to-talk release does not match an active capture'
    assert stt.calls == []
    data = transcript.json()
    assert data['messages'] == []
    assert data['session']['status'] == 'ready'
    event_types = [event['type'] for event in data['events']]
    assert 'ptt.started' in event_types
    assert 'ptt.stopped' not in event_types
    assert 'stt.final' not in event_types
    assert 'turn.committed' not in event_types


def test_cancel_then_typed_turn_keeps_canonical_transcript_flow_unchanged(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'typed reply'}, 'done': True}])
    stt = FakeSttService(text='should not run')
    app = make_app(tmp_path, transport=transport, stt_service=stt)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Cancel then typed'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        canceled = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/cancel')
        typed = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed question'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert canceled.status_code == 200
    assert typed.status_code == 201
    assert stt.calls == []
    data = transcript.json()
    assert data['session']['status'] == 'ready'
    assert [message['source'] for message in data['messages']] == ['typed_input', 'model_output']
    assert [message['text'] for message in data['messages']] == ['typed question', 'typed reply']




def test_early_speech_can_begin_from_streaming_assistant_message_when_sentence_is_stable(tmp_path: Path) -> None:
    tts = FakeTtsService(audio_bytes=b'RIFFearly')
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(db, status='streaming', text="Well, let's take a look. Still thinking")

    synthesized = speech.synthesize_message(session.id, message.id, text="Well, let's take a look.")
    start_state = asyncio.run(speech.start_playback(session.id, message.id))
    stop_state = asyncio.run(speech.stop_playback(session.id, message.id, reason='ended'))
    transcript = db.get_session(session.id)

    assert synthesized.audio_bytes == b'RIFFearly'
    assert tts.calls == ["Well, let's take a look."]
    assert start_state is None
    assert stop_state == 'speaking'
    assert transcript is not None
    assert transcript.status == 'speaking'


def test_chunked_speech_does_not_repeat_already_spoken_text_and_speaks_final_tail(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(db, status='streaming', text='')

    speech.synthesize_message(session.id, message.id, text='First sentence.')
    db.update_message(message.id, text='First sentence. Second sentence without punctuation', status='completed', updated_at=datetime.now(timezone.utc).isoformat(), completed_at=datetime.now(timezone.utc).isoformat(), metadata=message.metadata)
    speech.synthesize_message(session.id, message.id, text='Second sentence without punctuation')
    updated = db.list_messages(session.id)[-1]

    assert tts.calls == ['First sentence.', 'Second sentence without punctuation']
    assert updated.text == 'First sentence. Second sentence without punctuation'
    assert 'content' not in updated.model_dump()


def test_chunked_tts_still_sanitizes_stage_directions_without_mutating_canonical_text(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(db, status='streaming', text='Sure [laughs] hi (pause) there *chuckles* friend')

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['Sure, hi, there, friend']
    assert stored.text == 'Sure [laughs] hi (pause) there *chuckles* friend'


def test_assistant_speech_strips_markdown_emphasis_for_tts_only(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': "table didn't even *breathe* when"}, 'done': True}])
    tts = FakeTtsService(audio_bytes=b'RIFFspeech')
    app = make_app(tmp_path, transport=transport, tts_adapter=tts)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speech markdown emphasis'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        assistant = transcript['messages'][-1]
        speech = client.get(f"/api/v1/sessions/{session_id}/messages/{assistant['id']}/speech")
        updated = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert speech.status_code == 200
    assert tts.calls == ["table didn't even breathe when"]
    assert updated['messages'][-1]['text'] == "table didn't even *breathe* when"


def test_tts_sanitization_strips_double_asterisk_and_underscore_emphasis(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(
        db,
        status='streaming',
        text='This is **really** _important_ and __very__ clear.',
    )

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['This is really important and very clear.']
    assert stored.text == 'This is **really** _important_ and __very__ clear.'


def test_tts_sanitization_normalizes_ellipses_dashes_and_repeated_punctuation_for_speech_only(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(
        db,
        status='streaming',
        text='Well... this — maybe?? yes!!',
    )

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['Well, this, maybe? yes!']
    assert stored.text == 'Well... this — maybe?? yes!!'


def test_chunked_speech_strips_markdown_emphasis_without_mutating_canonical_text(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(db, status='streaming', text='')

    speech.synthesize_message(session.id, message.id, text='First *chunk*.')
    db.update_message(
        message.id,
        text='First *chunk*. Then **second** _chunk_ and __tail__.',
        status='completed',
        updated_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        metadata=message.metadata,
    )
    speech.synthesize_message(session.id, message.id, text='Then **second** _chunk_ and __tail__.')
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['First chunk.', 'Then second chunk and tail.']
    assert stored.text == 'First *chunk*. Then **second** _chunk_ and __tail__.'


def test_completed_chunk_handoff_holds_speaking_briefly_before_returning_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import speech as speech_module

    monkeypatch.setattr(speech_module, 'SPEECH_GAP_HOLD_SECONDS', 0.04)
    db, speech = make_speech_service(tmp_path, tts_adapter=FakeTtsService())
    session, message = seed_session_with_assistant_message(db, status='completed', text='All wrapped up.')

    async def exercise() -> str:
        await speech.start_playback(session.id, message.id)
        stop_state = await speech.stop_playback(session.id, message.id, reason='ended')
        await asyncio.sleep(0.06)
        return stop_state

    stop_state = asyncio.run(exercise())
    transcript = db.get_session(session.id)
    state_events = [event for event in db.list_events(session.id) if event.type == 'state']

    assert stop_state == 'speaking'
    assert transcript is not None
    assert transcript.status == 'ready'
    assert any(event.payload['state'] == 'ready' and event.payload['detail'] == 'tts_stopped' for event in state_events)


def test_completed_chunk_handoff_cancels_ready_fallback_when_next_playback_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import speech as speech_module

    monkeypatch.setattr(speech_module, 'SPEECH_GAP_HOLD_SECONDS', 0.06)
    db, speech = make_speech_service(tmp_path, tts_adapter=FakeTtsService())
    session, message = seed_session_with_assistant_message(db, status='completed', text='First sentence. Second sentence.')

    async def exercise() -> str:
        await speech.start_playback(session.id, message.id)
        stop_state = await speech.stop_playback(session.id, message.id, reason='ended')
        await asyncio.sleep(0.02)
        await speech.start_playback(session.id, message.id)
        await asyncio.sleep(0.08)
        return stop_state

    stop_state = asyncio.run(exercise())
    refreshed = db.get_session(session.id)
    state_events = [event for event in db.list_events(session.id) if event.type == 'state']

    assert stop_state == 'speaking'
    assert refreshed is not None
    assert refreshed.status == 'speaking'
    assert all(event.payload['state'] != 'ready' for event in state_events)

def test_short_chunk_gap_holds_speaking_and_avoids_thinking_bounce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import speech as speech_module

    monkeypatch.setattr(speech_module, 'SPEECH_GAP_HOLD_SECONDS', 0.06)
    db, speech = make_speech_service(tmp_path, tts_adapter=FakeTtsService())
    session, message = seed_session_with_assistant_message(db, status='streaming', text='First sentence. Second sentence.')

    async def exercise() -> str:
        await speech.start_playback(session.id, message.id)
        stop_state = await speech.stop_playback(session.id, message.id, reason='ended')
        await asyncio.sleep(0.02)
        await speech.start_playback(session.id, message.id)
        await asyncio.sleep(0.08)
        return stop_state

    stop_state = asyncio.run(exercise())
    refreshed = db.get_session(session.id)
    state_events = [event for event in db.list_events(session.id) if event.type == 'state']
    assert stop_state == 'speaking'
    assert refreshed is not None
    assert refreshed.status == 'speaking'
    assert all(event.payload['state'] != 'thinking' for event in state_events)


def test_long_chunk_gap_falls_back_from_speaking_to_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import speech as speech_module

    monkeypatch.setattr(speech_module, 'SPEECH_GAP_HOLD_SECONDS', 0.04)
    db, speech = make_speech_service(tmp_path, tts_adapter=FakeTtsService())
    session, message = seed_session_with_assistant_message(db, status='streaming', text='First sentence only.')

    async def exercise() -> str:
        await speech.start_playback(session.id, message.id)
        stop_state = await speech.stop_playback(session.id, message.id, reason='ended')
        await asyncio.sleep(0.07)
        return stop_state

    stop_state = asyncio.run(exercise())
    refreshed = db.get_session(session.id)
    state_events = [event for event in db.list_events(session.id) if event.type == 'state']
    assert stop_state == 'speaking'
    assert refreshed is not None
    assert refreshed.status == 'thinking'
    assert any(event.payload['state'] == 'thinking' and event.payload['detail'] == 'tts_stopped_waiting_for_more' for event in state_events)


def test_assistant_speech_sanitizes_stage_directions_for_tts_only(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Sure [laughs] hi (pause) there *chuckles* friend'}, 'done': True}])
    tts = FakeTtsService(audio_bytes=b'RIFFspeech')
    app = make_app(tmp_path, transport=transport, tts_adapter=tts)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speech'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        assistant = transcript['messages'][-1]
        speech = client.get(f"/api/v1/sessions/{session_id}/messages/{assistant['id']}/speech")
        updated = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert speech.status_code == 200
    assert speech.content == b'RIFFspeech'
    assert tts.calls == ['Sure, hi, there, friend']
    assert len([message for message in updated['messages'] if message['role'] == 'assistant']) == 1
    assert updated['messages'][-1]['text'] == 'Sure [laughs] hi (pause) there *chuckles* friend'
    assert 'content' not in updated['messages'][-1]


def test_filtered_think_trace_never_reaches_tts_input(tmp_path: Path) -> None:
    transport = streaming_transport([
        {'message': {'content': '<think>hidden inner monologue'}, 'done': False},
        {'message': {'content': ' still hidden</think>Spoken final answer.'}, 'done': True},
    ])
    tts = FakeTtsService(audio_bytes=b'RIFFspeech')
    app = make_app(tmp_path, transport=transport, tts_adapter=tts)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'No think in speech'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        assistant = transcript['messages'][-1]
        speech = client.get(f"/api/v1/sessions/{session_id}/messages/{assistant['id']}/speech")

    assert speech.status_code == 200
    assert assistant['text'] == 'Spoken final answer.'
    assert tts.calls == ['Spoken final answer.']


def test_stale_speech_start_is_rejected_after_session_moves_into_newer_turn_state(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Older reply'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, stt_service=FakeSttService(text='voice question'), tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Stale speech state'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})
        assistant_id = turn.json()['assistant_message_id']

        started = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        stale_start = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_id}/speech/start')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert started.status_code == 200
    assert stale_start.status_code == 409
    assert stale_start.json()['detail'] == 'assistant speech start is stale for the current session state'
    assert transcript['session']['status'] == 'listening'
    assert all(event['type'] != 'tts.started' for event in transcript['events'])


def test_stale_speech_start_is_rejected_after_a_newer_completed_turn_and_does_not_overwrite_ready_state(tmp_path: Path) -> None:
    responses = iter([
        [{'message': {'content': 'First reply'}, 'done': True}],
        [{'message': {'content': 'Second reply'}, 'done': True}],
    ])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gemma3:4b'}]})
        chunks = next(responses)
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Stale speech latest'}).json()['id']
        first = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'First question'})
        second = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Second question'})
        stale_start = client.post(
            f"/api/v1/sessions/{session_id}/messages/{first.json()['assistant_message_id']}/speech/start"
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert first.status_code == 201
    assert second.status_code == 201
    assert stale_start.status_code == 409
    assert stale_start.json()['detail'] == 'assistant speech start is stale for the current session state'
    assert transcript['session']['status'] == 'ready'
    assert [message['text'] for message in transcript['messages'] if message['role'] == 'assistant'] == ['First reply', 'Second reply']
    assert len([event for event in transcript['events'] if event['type'] == 'tts.started']) == 0


def test_one_active_speech_playback_per_session_still_holds(tmp_path: Path) -> None:
    responses = iter([
        [{'message': {'content': 'Reply one'}, 'done': True}],
        [{'message': {'content': 'Reply two'}, 'done': True}],
    ])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gemma3:4b'}]})
        chunks = next(responses)
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'One playback'}).json()['id']
        first = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'First'})
        second = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Second'})
        latest_id = second.json()['assistant_message_id']
        older_id = first.json()['assistant_message_id']

        start_latest = client.post(f'/api/v1/sessions/{session_id}/messages/{latest_id}/speech/start')
        start_older_while_active = client.post(f'/api/v1/sessions/{session_id}/messages/{older_id}/speech/start')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert start_latest.status_code == 200
    assert start_older_while_active.status_code == 409
    assert transcript['session']['status'] == 'speaking'
    assert len([event for event in transcript['events'] if event['type'] == 'tts.started']) == 1
    assert len([event for event in transcript['events'] if event['type'] == 'state' and event['payload']['state'] == 'speaking']) == 1


def test_same_message_duplicate_speech_start_is_a_no_op_without_duplicate_events(tmp_path: Path) -> None:
    responses = iter([
        [{'message': {'content': 'Reply one'}, 'done': True}],
        [{'message': {'content': 'Reply two'}, 'done': True}],
    ])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'gemma3:4b'}]})
        chunks = next(responses)
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(200, content=body)

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speech idempotency'}).json()['id']
        first = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'First'})
        second = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Second'})
        latest_id = second.json()['assistant_message_id']
        older_id = first.json()['assistant_message_id']

        start_latest = client.post(f'/api/v1/sessions/{session_id}/messages/{latest_id}/speech/start')
        start_latest_again = client.post(f'/api/v1/sessions/{session_id}/messages/{latest_id}/speech/start')
        stale_start = client.post(f'/api/v1/sessions/{session_id}/messages/{older_id}/speech/start')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert start_latest.status_code == 200
    assert start_latest_again.status_code == 200
    assert start_latest_again.json() == {'status': 'speaking'}
    assert stale_start.status_code == 409
    assert stale_start.json()['detail'] == 'another assistant speech playback is already active for this session'
    assert transcript['session']['status'] == 'speaking'
    assert [message['text'] for message in transcript['messages'] if message['role'] == 'assistant'] == ['Reply one', 'Reply two']
    assert len([message for message in transcript['messages'] if message['role'] == 'assistant']) == 2
    assert len([event for event in transcript['events'] if event['type'] == 'tts.started']) == 1
    assert len([event for event in transcript['events'] if event['type'] == 'state' and event['payload']['state'] == 'speaking']) == 1


def test_speaking_state_and_tts_events_only_emit_during_actual_playback(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Speech state'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Speaking'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Go'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        assistant_id = transcript['messages'][-1]['id']
        start = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_id}/speech/start')
        stop = client.post(f'/api/v1/sessions/{session_id}/messages/{assistant_id}/speech/stop', json={'reason': 'typed_submit'})
        updated = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert start.status_code == 200
    assert stop.status_code == 200
    state_events = [event['payload']['state'] for event in updated['events'] if event['type'] == 'state']
    assert 'speaking' in state_events
    assert set(state_events).issubset(CONTRACT_TRANSCRIPT_STATES)
    event_types = [event['type'] for event in updated['events']]
    assert 'tts.started' in event_types
    assert 'tts.stopped' in event_types
    assert updated['session']['status'] == 'ready'


def test_tts_failure_does_not_destroy_completed_text_response(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Still text'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, tts_adapter=FakeTtsService(error='kokoro missing'))
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'TTS fail'}).json()['id']
        client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})
        transcript_before = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        assistant_id = transcript_before['messages'][-1]['id']
        speech = client.get(f'/api/v1/sessions/{session_id}/messages/{assistant_id}/speech')
        transcript_after = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert speech.status_code == 503
    assert transcript_before['messages'][-1]['text'] == 'Still text'
    assert transcript_after['messages'][-1]['text'] == 'Still text'
    assert transcript_after['messages'][-1]['status'] == 'completed'


def test_voice_turn_and_typed_chat_still_work_with_speech_enabled(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'Voice ok'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, stt_service=FakeSttService(text='voice turn text'), tts_adapter=FakeTtsService())
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Voice + typed'}).json()['id']
        start = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        voice = client.post(f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm', content=b'voice-bytes', headers={'content-type': 'audio/webm'})
        typed = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'typed still works'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert start.status_code == 200
    assert voice.status_code == 201
    assert typed.status_code == 201
    assert [message['source'] for message in transcript['messages'] if message['role'] == 'user'] == ['voice_input', 'typed_input']


def test_readiness_endpoint_reports_warmup_state(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'OK'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, ollama_warmup_enabled=True, tts_warmup_enabled=False)
    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get('/api/v1/readiness')
            if readiness.json()['checks']['ollama']['status'] != 'pending':
                break
        config = client.get('/api/v1/config')

    assert readiness.status_code == 200
    body = readiness.json()
    assert body['local_only'] is True
    assert body['checks']['ollama']['status'] == 'ready'
    assert body['checks']['tts']['status'] == 'not_run'
    assert body['runtime']['ollama']['keep_alive'] == '30m'
    assert body['runtime']['ollama']['num_ctx'] == 8192
    assert body['runtime']['ollama']['num_parallel'] == 1
    assert body['runtime']['stt']['requested_device'] == 'auto'
    assert body['runtime']['stt']['selected_device'] in {'cpu', 'cuda'}
    assert body['runtime']['stt']['requested_compute_type'] == 'auto'
    assert body['runtime']['stt']['resolved_compute_type'] in {'int8', 'int8_float16'}
    assert config.json()['ollama_warmup_enabled'] is True
    assert config.json()['tts_warmup_enabled'] is False


def test_readiness_reports_installed_model_even_when_warmup_disabled(tmp_path: Path) -> None:
    transport = streaming_transport([{'message': {'content': 'OK'}, 'done': True}])
    app = make_app(tmp_path, transport=transport, ollama_warmup_enabled=False, tts_warmup_enabled=False)
    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get('/api/v1/readiness')
            if readiness.json()['checks']['ollama']['status'] != 'pending':
                break

    assert readiness.status_code == 200
    body = readiness.json()
    assert body['checks']['ollama']['status'] == 'ready'
    assert body['checks']['ollama']['detail'] == 'Model gemma3:4b is installed locally.'
    assert body['checks']['tts']['status'] == 'not_run'


def test_readiness_fails_when_configured_model_missing_and_warmup_disabled(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'llama3:8b'}]})
        return httpx.Response(404, json={'error': 'model gemma3:4b not found'})

    app = make_app(
        tmp_path,
        transport=httpx.MockTransport(handler),
        ollama_model='gemma3:4b',
        ollama_warmup_enabled=False,
        tts_warmup_enabled=False,
    )
    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get('/api/v1/readiness')
            if readiness.json()['checks']['ollama']['status'] != 'pending':
                break

    assert readiness.status_code == 200
    body = readiness.json()
    assert body['checks']['ollama']['status'] == 'failed'
    assert 'configured Ollama model is not installed locally: gemma3:4b' in body['checks']['ollama']['detail']
    assert 'Run `ollama pull gemma3:4b`' in body['checks']['ollama']['detail']


def test_readiness_fails_when_ollama_unavailable_and_warmup_disabled(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('Connection refused')

    app = make_app(
        tmp_path,
        transport=httpx.MockTransport(handler),
        ollama_warmup_enabled=False,
        tts_warmup_enabled=False,
    )
    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get('/api/v1/readiness')
            if readiness.json()['checks']['ollama']['status'] != 'pending':
                break

    assert readiness.status_code == 200
    body = readiness.json()
    assert body['checks']['ollama']['status'] == 'failed'
    assert 'Connection refused' in body['checks']['ollama']['detail']


def test_readiness_and_turn_fail_clearly_when_configured_model_is_missing(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'llama3:8b'}]})
        return httpx.Response(404, json={'error': 'model gemma3:4b not found'})

    app = make_app(
        tmp_path,
        transport=httpx.MockTransport(handler),
        ollama_model='gemma3:4b',
        ollama_warmup_enabled=True,
    )
    with TestClient(app) as client:
        for _ in range(20):
            readiness = client.get('/api/v1/readiness')
            if readiness.json()['checks']['ollama']['status'] != 'pending':
                break
        session_id = client.post('/api/v1/sessions', json={'title': 'Missing model'}).json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hello'})

    assert readiness.status_code == 200
    assert readiness.json()['checks']['ollama']['status'] == 'failed'
    assert 'configured Ollama model is not installed locally: gemma3:4b' in readiness.json()['checks']['ollama']['detail']
    assert turn.status_code == 503
    assert 'configured Ollama model is not installed locally: gemma3:4b' in turn.json()['detail']
