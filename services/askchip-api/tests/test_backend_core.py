from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_app(tmp_path: Path, transport: httpx.AsyncBaseTransport | None = None):
    config = Settings(database_path=tmp_path / 'askchip.db')
    return create_app(config=config, ollama_transport=transport)


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


def test_typed_turn_commits_and_assembles_assistant_message(tmp_path: Path) -> None:
    transport = streaming_transport(
        [
            {'message': {'content': 'Hello'}, 'done': False},
            {'message': {'content': ' world'}, 'done': False},
            {'message': {'content': '!'}, 'done': True},
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
    assert data['messages'][0]['content'] == 'Hi'
    assert data['messages'][1]['content'] == 'Hello world!'
    assert data['messages'][1]['status'] == 'completed'
    event_types = [event['type'] for event in data['events']]
    assert 'turn.committed' in event_types
    assert 'assistant.started' in event_types
    assert event_types.count('assistant.delta') == 3
    assert 'assistant.completed' in event_types


def test_one_active_assistant_job_enforced(tmp_path: Path) -> None:
    start = asyncio.Event()
    finish = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        start.set()
        await finish.wait()
        body = json.dumps({'message': {'content': 'done'}, 'done': True}).encode() + b'\n'
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    app = make_app(tmp_path, transport=transport)

    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Concurrent'}).json()['id']
        results: dict[str, object] = {}

        def first_turn() -> None:
            results['first'] = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'One'})

        thread = __import__('threading').Thread(target=first_turn)
        thread.start()
        asyncio.run(asyncio.wait_for(start.wait(), timeout=2))
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
    event_types = [event['type'] for event in transcript.json()['events']]
    assert event_types[0] == 'connection.lifecycle'
    assert 'state' in event_types


def test_ollama_unavailable_path(tmp_path: Path) -> None:
    transport = streaming_transport([], status_code=503)
    app = make_app(tmp_path, transport=transport)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'Errors'}).json()['id']
        response = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hi'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert response.status_code == 503
    events = transcript.json()['events']
    assert any(event['type'] == 'error' and event['payload']['code'] == 'ollama_unavailable' for event in events)
    assert transcript.json()['messages'][-1]['status'] == 'error'
