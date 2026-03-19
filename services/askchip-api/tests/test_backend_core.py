from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app
from app.prompting import PromptAssembler

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
CONTRACT_TRANSCRIPT_STATES = {'ready', 'thinking', 'error'}
CONTRACT_SOURCES_BY_ROLE = {'user': 'typed_input', 'assistant': 'model_output'}
CONTRACT_SOURCE_VOCABULARY = {'typed_input', 'voice_input', 'model_output', 'system_notice'}


def make_app(tmp_path: Path, transport: httpx.AsyncBaseTransport | None = None, webrtc_peer_factory=None, **settings_overrides):
    config = Settings(database_path=tmp_path / 'askchip.db', **settings_overrides)
    return create_app(config=config, ollama_transport=transport, webrtc_peer_factory=webrtc_peer_factory)




class FakeManagedPeer:
    def __init__(self) -> None:
        self.closed = False

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


def streaming_transport(chunks: list[dict[str, object]], status_code: int = 200) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = b''.join(json.dumps(chunk).encode() + b'\n' for chunk in chunks)
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


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
        assert message['source'] == CONTRACT_SOURCES_BY_ROLE[message['role']]
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

    assert messages[0].role == 'system'
    assert 'Nebraska ex-farmer turned techy' in messages[0].text
    assert messages[-2].text == 'recent'
    assert messages[-1].model_dump() == {'role': 'user', 'text': 'new question'}


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

    config = load_settings()

    assert config.host == '0.0.0.0'
    assert config.port == 9000
    assert str(config.database_path) == '/tmp/askchip.db'
    assert config.prompt_transcript_window == 4


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
