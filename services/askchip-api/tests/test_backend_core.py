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
from app.vmware_conversation_policy import decide_vmware_next_move
from app.webrtc.session_store import WebRtcSessionStore
from app.api_models import VmwareTriageState
from app.expert_desk_metadata import (
    build_vmware_trajectory_transition_payloads,
    build_vmware_handoff_packet,
    normalize_vmware_resolution_status,
    read_expert_desk_metadata,
    update_vmware_triage_state,
)

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




def test_default_settings_use_gemma_model_and_am_echo_voice() -> None:
    config = Settings()

    assert config.ollama_model == 'gemma3:4b'
    assert config.ollama_num_parallel == 1
    assert config.tts_voice == 'am_echo'


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
    assert response.json()['tts_voice'] == 'am_echo'
    assert response.json()['tts_requested_device'] == 'auto'
    assert response.json()['tts_provider'] in {'CPUExecutionProvider', 'CUDAExecutionProvider', 'unknown'}
    assert isinstance(response.json()['tts_available_providers'], list)
    assert response.json()['max_artifact_upload_bytes'] == 5 * 1024 * 1024
    assert 'tts_warning' in response.json()
    assert 'tts_fallback_reason' in response.json()


def test_config_and_readiness_surface_tts_auto_cpu_fallback_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tts as tts_module

    monkeypatch.setattr(tts_module, '_available_onnx_providers', lambda: ['CPUExecutionProvider'])
    app = make_app(tmp_path, tts_device='auto')
    with TestClient(app) as client:
        config = client.get('/api/v1/config')
        readiness = client.get('/api/v1/readiness')

    assert config.status_code == 200
    assert readiness.status_code == 200
    config_body = config.json()
    readiness_body = readiness.json()
    assert config_body['tts_requested_device'] == 'auto'
    assert config_body['tts_device'] == 'cpu'
    assert config_body['tts_provider'] == 'CPUExecutionProvider'
    assert config_body['tts_fallback_reason'] == 'ASKCHIP_TTS_DEVICE=auto requested but CUDAExecutionProvider is unavailable.'
    assert config_body['tts_warning'] == 'ASKCHIP_TTS_DEVICE=auto requested but CUDAExecutionProvider is unavailable. Using CPUExecutionProvider.'
    assert readiness_body['runtime']['tts']['fallback_reason'] == 'ASKCHIP_TTS_DEVICE=auto requested but CUDAExecutionProvider is unavailable.'
    assert readiness_body['runtime']['tts']['warning'] == 'ASKCHIP_TTS_DEVICE=auto requested but CUDAExecutionProvider is unavailable. Using CPUExecutionProvider.'


def test_local_readme_documents_gpu_tts_setup_and_diagnostics() -> None:
    readme_text = (Path(__file__).resolve().parents[3] / 'docs/askchip-local/README.md').read_text(encoding='utf-8')

    assert 'services/askchip-api/.venv' in readme_text
    assert 'setup-askchip-local-windows-nvidia.ps1' in readme_text
    assert 'CUDAExecutionProvider' in readme_text
    assert '/api/v1/config' in readme_text
    assert '/api/v1/readiness' in readme_text


def test_windows_nvidia_setup_script_documents_gpu_runtime_swap_and_provider_validation() -> None:
    script_text = (Path(__file__).resolve().parents[3] / 'scripts/setup-askchip-local-windows-nvidia.ps1').read_text(encoding='utf-8')

    assert 'services/askchip-api/.venv' in script_text
    assert 'onnxruntime-gpu' in script_text
    assert 'uninstall -y onnxruntime' in script_text
    assert 'CUDAExecutionProvider' in script_text


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


def test_typed_turn_prompt_preface_includes_expert_desk_metadata(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Got it.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Expert session',
                'metadata': {
                    'expert_desk': {
                        'issue_category': 'Backup failure',
                        'environment_platform': 'AWS',
                        'urgency': 'Critical',
                        'issue_description': 'Nightly snapshot job failed.',
                        'architecture_notes': 'Cross-region backups enabled.',
                        'error_text': 'AccessDenied on backup vault',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-aws-engineer',
                        'expert_persona_label': 'AI AWS Engineer',
                        'expert_persona_summary': 'AWS specialist summary',
                        'request_label': 'Case A-1',
                        'preferred_expert_type': 'Cloud',
                        'recommended_expert_type': 'Cloud',
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'How do we stabilize this?'})

    assert turn.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    system_messages = [message for message in payload['messages'] if message['role'] == 'system']
    assert len(system_messages) >= 3
    assert 'AI AWS Engineer' in system_messages[1]['content']
    assert 'selected expert persona id: ai-aws-engineer' in system_messages[2]['content']
    assert 'issue category: Backup failure' in system_messages[2]['content']
    assert 'environment/platform: AWS' in system_messages[2]['content']
    assert 'urgency: Critical' in system_messages[2]['content']
    assert 'issue description: Nightly snapshot job failed.' in system_messages[2]['content']
    assert 'architecture notes: Cross-region backups enabled.' in system_messages[2]['content']
    assert 'error text: AccessDenied on backup vault' in system_messages[2]['content']
    assert 'recommended path: continue_with_ai_now' in system_messages[2]['content']


def test_voice_turn_prompt_preface_includes_expert_desk_metadata(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Starting triage now.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), stt_service=FakeSttService(text='voice question'))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Expert voice session',
                'metadata': {
                    'expert_desk': {
                        'issue_category': 'Replication lag',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'issue_description': 'Replication queue is growing.',
                        'recommended_path': 'launch_live_session_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': 'VMware specialist summary',
                        'request_label': 'Case V-2',
                        'preferred_expert_type': 'Virtualization',
                        'recommended_expert_type': 'Virtualization',
                        'architecture_notes': '',
                        'error_text': '',
                    }
                },
            },
        )
        session_id = created.json()['id']
        start = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        voice = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'content-type': 'audio/webm'},
        )

    assert start.status_code == 200
    assert voice.status_code == 201
    payload = captured['payload']
    assert isinstance(payload, dict)
    system_messages = [message for message in payload['messages'] if message['role'] == 'system']
    assert len(system_messages) >= 3
    assert 'AI VMware Engineer' in system_messages[1]['content']
    assert 'selected expert persona id: ai-vmware-engineer' in system_messages[2]['content']
    assert 'issue category: Replication lag' in system_messages[2]['content']
    assert 'environment/platform: VMware' in system_messages[2]['content']
    assert 'recommended path: launch_live_session_now' in system_messages[2]['content']


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


def test_delete_session_removes_stored_artifact_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Delete artifacts',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'req',
                        'issue_category': 'production-outage',
                        'environment_platform': 'vmware',
                        'urgency': 'same-day',
                        'preferred_expert_type': 'ai-vmware-engineer',
                        'recommended_expert_type': 'ai-vmware-engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': '',
                        'issue_description': 'issue',
                        'architecture_notes': '',
                        'error_text': '',
                    },
                },
            },
        ).json()['id']
        upload = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 ERROR vmfs datastore issue',
            headers={'X-Artifact-Filename': 'vmkernel.log', 'Content-Type': 'text/plain'},
        )
        storage_path = Path(upload.json()['artifact']['storage_path'])
        session_dir = storage_path.parent
        deleted = client.delete(f'/api/v1/sessions/{session_id}')

    assert deleted.status_code == 200
    assert storage_path.exists() is False
    assert session_dir.exists() is False


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

    assert messages[0].role == 'system'
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
    assert user_entries == ['plain prior text', 'fresh plain input']


def test_prompt_assembler_uses_expert_desk_preface_and_persona_overlay() -> None:
    assembler = PromptAssembler(transcript_window=2)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='prior question', turn_id='t1', source='typed_input'),
    ]
    session_metadata = {
        'expert_desk': {
            'issue_category': 'Storage outage',
            'environment_platform': 'VMware vSphere',
            'urgency': 'High',
            'issue_description': 'Datastore latency spiked and VMs froze.',
            'architecture_notes': 'Two clusters with shared SAN replication.',
            'error_text': 'APD timeout on host 03',
            'recommended_path': 'launch_live_session_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'expert_persona_summary': 'VMware specialist summary',
            'request_label': 'Case 204',
            'recommended_expert_type': 'Virtualization',
            'preferred_expert_type': 'Virtualization',
        }
    }

    messages = assembler.build_messages(
        transcript,
        user_text='What should we do first?',
        session_metadata=session_metadata,
    )

    assert messages[0].role == 'system'
    assert 'You are AskChip Expert Desk' in messages[0].text
    assert messages[1].role == 'system'
    assert 'AI VMware Engineer' in messages[1].text
    assert 'selected expert persona id: ai-vmware-engineer' in messages[2].text
    assert messages[2].role == 'system'
    assert 'issue category: Storage outage' in messages[2].text
    assert 'environment/platform: VMware vSphere' in messages[2].text
    assert 'urgency: High' in messages[2].text
    assert 'issue description: Datastore latency spiked and VMs froze.' in messages[2].text
    assert 'recommended path: launch_live_session_now' in messages[2].text
    assert 'architecture notes: Two clusters with shared SAN replication.' in messages[2].text
    assert 'error text: APD timeout on host 03' in messages[2].text
    assert messages[-1].text == 'What should we do first?'


def test_prompt_assembler_vmware_kickoff_guidance_reflects_log_receipt() -> None:
    assembler = PromptAssembler(transcript_window=3)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='Need help with outage', turn_id='t1', source='typed_input'),
    ]
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req kickoff 1',
            'environment_platform': 'VMware',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'issue_description': 'Hosts disconnected',
            'issue_category': 'Production outage',
            'urgency': 'High',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_available': True,
            'uploaded_logs_count': 2,
            'uploaded_log_names': ['vpxd.log', 'vmkernel.log'],
        }
    }

    messages = assembler.build_messages(transcript, user_text='What should we do first?', session_metadata=session_metadata)

    runtime_guidance = next(message for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))
    assert 'This is your first VMware response in the live session.' in runtime_guidance.text
    assert 'The first response is a conversational opener, not an assessment dump.' in runtime_guidance.text
    assert 'Keep the first response to 2-3 short sentences by default.' in runtime_guidance.text
    assert 'Do not use headings (for example: initial assessment, likely diagnosis path, immediate next actions).' in runtime_guidance.text
    assert 'Do not use numbered checklists unless the user explicitly asks for one.' in runtime_guidance.text
    assert 'End with one focused next question.' in runtime_guidance.text
    assert 'Uploaded logs available: yes.' in runtime_guidance.text
    assert 'Uploaded log file names: vpxd.log, vmkernel.log.' in runtime_guidance.text
    assert 'acknowledge receipt, say you can review them' in runtime_guidance.text
    assert 'Keep responses short by default: usually 2-4 sentences unless the user asks for more.' in runtime_guidance.text
    assert 'Ask one focused next question at a time to move triage forward.' in runtime_guidance.text


def test_prompt_assembler_vmware_kickoff_guidance_explicit_no_logs_path() -> None:
    assembler = PromptAssembler(transcript_window=3)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='Need help with latency', turn_id='t1', source='typed_input'),
    ]
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req kickoff 2',
            'environment_platform': 'VMware',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'issue_description': 'Storage latency',
            'issue_category': 'Performance issue',
            'urgency': 'Medium',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_available': False,
            'uploaded_logs_count': 0,
        }
    }

    messages = assembler.build_messages(transcript, user_text='Can we start now?', session_metadata=session_metadata)

    runtime_guidance = next(message for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))
    assert 'Uploaded logs available: no.' in runtime_guidance.text
    assert 'If logs were not received, briefly say that' in runtime_guidance.text
    assert 'point to the live-session upload control.' in runtime_guidance.text
    assert 'recommend uploading: vCenter logs, ESXi host/support bundle, vmkernel.log, and vpxd.log,' in runtime_guidance.text


def test_prompt_assembler_vmware_followup_guidance_handles_live_uploads() -> None:
    assembler = PromptAssembler(transcript_window=4)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='Need help with outage', turn_id='t1', source='typed_input'),
        MessageRecord(session_id='s', role='assistant', text='Tell me more.', turn_id='t1', source='model_output'),
        MessageRecord(session_id='s', role='user', text='I just uploaded logs.', turn_id='t2', source='typed_input'),
    ]
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req followup 1',
            'environment_platform': 'VMware',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'issue_description': 'Hosts disconnected',
            'issue_category': 'Production outage',
            'urgency': 'High',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_available': True,
            'uploaded_logs_count': 1,
            'uploaded_log_names': ['support-bundle.tgz'],
        }
    }

    messages = assembler.build_messages(transcript, user_text='Can you review them?', session_metadata=session_metadata)

    runtime_guidance = next(message for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))
    assert 'If logs were just uploaded during this live session' in runtime_guidance.text
    assert 'Do not fabricate findings from logs that were not parsed.' in runtime_guidance.text
    assert 'For follow-up turns, give one or two likely issue paths and one or two short verification steps when grounded by evidence.' in runtime_guidance.text
    assert 'Avoid broad speculation or generic outage declarations without concrete evidence from user context.' in runtime_guidance.text



def test_prompt_assembler_vmware_metadata_only_guidance_is_honest() -> None:
    assembler = PromptAssembler(transcript_window=4)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='Need help with outage', turn_id='t1', source='typed_input'),
        MessageRecord(session_id='s', role='assistant', text='I can help. What changed right before impact?', turn_id='t1', source='model_output'),
    ]
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req followup 2',
            'environment_platform': 'VMware',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'issue_description': 'Hosts disconnected',
            'issue_category': 'Production outage',
            'urgency': 'High',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_available': True,
            'uploaded_logs_count': 1,
            'uploaded_log_names': ['esxi-support-bundle.tgz'],
        }
    }

    messages = assembler.build_messages(transcript, user_text='Any early read from those logs?', session_metadata=session_metadata)

    runtime_guidance = next(message for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))
    assert 'Never claim parsed-log findings unless parsed content is explicitly present in context.' in runtime_guidance.text
    assert 'If only metadata is available, say logs were received but not parsed yet, and state what you would check next.' in runtime_guidance.text


def test_prompt_assembler_includes_vmware_triage_log_guidance_fields() -> None:
    assembler = PromptAssembler(transcript_window=4)
    from app.domain_models import MessageRecord

    transcript = [
        MessageRecord(session_id='s', role='user', text='Need help with host disconnects', turn_id='t1', source='typed_input'),
        MessageRecord(session_id='s', role='assistant', text='Understood. What changed right before impact?', turn_id='t1', source='model_output'),
    ]
    session_metadata = {
        'expert_desk': {
            'request_label': 'Request: VMware outage',
            'environment_platform': 'VMware',
            'issue_description': 'Hosts disconnected',
            'issue_category': 'Production outage',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_available': True,
            'uploaded_logs_count': 1,
            'uploaded_log_names': ['vpxd.log'],
            'vmware_triage': {
                'issue_family': 'vcenter-services',
                'log_sufficiency_status': 'partial',
                'missing_logs': ['vCenter Server logs'],
                'optional_logs': ['vSphere UI/API gateway logs'],
                'log_guidance_summary': 'Some required logs are present, but additional vCenter service logs are still needed.',
            },
        }
    }

    messages = assembler.build_messages(transcript, user_text='Can you continue triage?', session_metadata=session_metadata)
    prebrief = next(message.text for message in messages if message.role == 'system' and message.text.startswith('Expert Desk session pre-brief:'))
    runtime_guidance = next(message.text for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))

    assert 'vmware triage log sufficiency status: partial' in prebrief
    assert 'vmware triage missing logs: vCenter Server logs' in prebrief
    assert 'vmware triage optional logs: vSphere UI/API gateway logs' in prebrief
    assert 'vmware triage log guidance summary: Some required logs are present' in prebrief
    assert 'If vmware triage log sufficiency status is partial, say you can proceed with current evidence but explicitly list missing logs.' in runtime_guidance
    assert 'Deterministic policy next move:' in runtime_guidance
    assert 'Deterministic policy working hypothesis:' in runtime_guidance
    assert 'Deterministic policy focused next question:' in runtime_guidance


def test_vmware_policy_first_turn_prefers_hypothesis_confirmation() -> None:
    decision = decide_vmware_next_move(
        triage_state=VmwareTriageState(
            issue_family='host-networking',
            confidence=0.66,
            conversation_stage='hypothesis_confirmation',
            log_sufficiency_status='partial',
            missing_logs=['vCenter Server logs'],
        ),
        latest_user_feedback='We saw host disconnect alarms right after the vDS change.',
        has_prior_assistant_turn=False,
    )

    assert decision.next_move == 'confirm_scope'
    assert 'Working hypothesis:' in decision.working_hypothesis
    assert 'Is the impact isolated' in decision.focused_question


def test_vmware_policy_user_correction_updates_next_move() -> None:
    decision = decide_vmware_next_move(
        triage_state=VmwareTriageState(
            issue_family='host-networking',
            confidence=0.8,
            conversation_stage='mitigation',
            log_sufficiency_status='sufficient',
        ),
        latest_user_feedback='No, that is not right. It started before the vDS change and only impacts one datastore cluster.',
        has_prior_assistant_turn=True,
    )

    assert decision.user_feedback_signal == 'correction'
    assert decision.next_move == 'validate_hypothesis'


def test_vmware_policy_requests_missing_logs_for_current_issue_family() -> None:
    decision = decide_vmware_next_move(
        triage_state=VmwareTriageState(
            issue_family='vcenter-services',
            confidence=0.74,
            conversation_stage='evidence_gathering',
            log_sufficiency_status='insufficient',
            missing_logs=['vCenter Server logs', 'vpxd.log'],
        ),
        latest_user_feedback='yes that matches',
        has_prior_assistant_turn=True,
    )

    assert decision.next_move == 'request_missing_logs'
    assert 'upload vCenter Server logs, vpxd.log' in decision.focused_question


@pytest.mark.parametrize(
    ('previous_status', 'current_status'),
    [
        ('insufficient', 'partial'),
        ('partial', 'sufficient'),
    ],
)
def test_vmware_transition_event_payloads_emit_on_log_sufficiency_change(previous_status: str, current_status: str) -> None:
    previous_metadata = {
        'expert_desk': {
            'request_label': 'Req',
            'issue_category': 'Host issue',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Host disconnects',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'issue_family': 'host-networking',
                'conversation_stage': 'log_collection',
                'policy_next_move': 'request_missing_logs',
                'log_sufficiency_status': previous_status,
                'resolution_status': 'blocked_waiting_on_logs',
            },
        }
    }
    current_metadata = {
        **previous_metadata,
        'expert_desk': {
            **previous_metadata['expert_desk'],
            'vmware_triage': {
                **previous_metadata['expert_desk']['vmware_triage'],
                'log_sufficiency_status': current_status,
            },
        },
    }

    payloads = build_vmware_trajectory_transition_payloads(
        previous_metadata,
        current_metadata,
        source_path='session_patch_refresh',
    )

    assert payloads == [
        (
            'vmware.trajectory.log_sufficiency_changed',
            {
                'previous_value': previous_status,
                'current_value': current_status,
                'source_path': 'session_patch_refresh',
            },
        )
    ]


def test_vmware_transition_event_payloads_emit_only_when_values_change() -> None:
    previous_metadata = {
        'expert_desk': {
            'request_label': 'Req',
            'issue_category': 'Host issue',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Host disconnects',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'issue_family': 'host-networking',
                'conversation_stage': 'scope_confirmation',
                'policy_next_move': 'confirm_scope',
                'log_sufficiency_status': 'partial',
                'resolution_status': 'unresolved',
            },
        }
    }
    current_metadata = {
        **previous_metadata,
        'expert_desk': {
            **previous_metadata['expert_desk'],
            'vmware_triage': {
                **previous_metadata['expert_desk']['vmware_triage'],
                'conversation_stage': 'hypothesis_validation',
                'policy_next_move': 'validate_hypothesis',
            },
        },
    }

    payloads = build_vmware_trajectory_transition_payloads(
        previous_metadata,
        current_metadata,
        source_path='turn_runtime',
        turn_id='turn-123',
        trace_id='trace-123',
    )

    assert payloads == [
        (
            'vmware.trajectory.conversation_stage_changed',
            {
                'previous_value': 'scope_confirmation',
                'current_value': 'hypothesis_validation',
                'source_path': 'turn_runtime',
                'turn_id': 'turn-123',
                'trace_id': 'trace-123',
            },
        ),
        (
            'vmware.trajectory.next_move_changed',
            {
                'previous_value': 'confirm_scope',
                'current_value': 'validate_hypothesis',
                'source_path': 'turn_runtime',
                'turn_id': 'turn-123',
                'trace_id': 'trace-123',
            },
        ),
    ]


def test_vmware_transition_event_payloads_track_issue_family_and_resolution_changes() -> None:
    previous_metadata = {
        'expert_desk': {
            'request_label': 'Req',
            'issue_category': 'Host issue',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Host disconnects',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'issue_family': 'host-networking',
                'conversation_stage': 'verification',
                'policy_next_move': 'verify_result',
                'log_sufficiency_status': 'sufficient',
                'resolution_status': 'monitoring',
            },
        }
    }
    current_metadata = {
        **previous_metadata,
        'expert_desk': {
            **previous_metadata['expert_desk'],
            'vmware_triage': {
                **previous_metadata['expert_desk']['vmware_triage'],
                'issue_family': 'storage-pathing',
                'resolution_status': 'needs_human_handoff',
            },
        },
    }

    payloads = build_vmware_trajectory_transition_payloads(
        previous_metadata,
        current_metadata,
        source_path='turn_runtime',
    )
    assert payloads == [
        (
            'vmware.trajectory.issue_family_changed',
            {
                'previous_value': 'host-networking',
                'current_value': 'storage-pathing',
                'source_path': 'turn_runtime',
            },
        ),
        (
            'vmware.trajectory.resolution_status_changed',
            {
                'previous_value': 'monitoring',
                'current_value': 'needs_human_handoff',
                'source_path': 'turn_runtime',
            },
        ),
    ]


def test_vmware_trajectory_regression_harness_policy_paths_across_issue_families() -> None:
    scenarios = [
        {
            'issue_family': 'host-networking',
            'log_sufficiency_status': 'insufficient',
            'resolution_status': 'blocked_waiting_on_logs',
            'feedback': 'We have vmkernel only so far.',
            'expected_move': 'request_missing_logs',
        },
        {
            'issue_family': 'storage-pathing',
            'log_sufficiency_status': 'partial',
            'resolution_status': 'unresolved',
            'feedback': 'No, that is not right; this started after SAN maintenance.',
            'expected_move': 'validate_hypothesis',
        },
        {
            'issue_family': 'vcenter-services',
            'log_sufficiency_status': 'sufficient',
            'resolution_status': 'blocked_waiting_on_user_action',
            'feedback': 'I can run a safe step now.',
            'expected_move': 'propose_safe_next_step',
        },
        {
            'issue_family': 'vm-performance',
            'log_sufficiency_status': 'sufficient',
            'resolution_status': 'needs_human_handoff',
            'feedback': 'Still degraded across both clusters.',
            'expected_move': 'handoff_required',
        },
        {
            'issue_family': 'vm-performance',
            'log_sufficiency_status': 'sufficient',
            'resolution_status': 'resolved',
            'feedback': 'Performance is stable now.',
            'expected_move': 'resolution_confirmed',
        },
    ]

    for scenario in scenarios:
        decision = decide_vmware_next_move(
            triage_state=VmwareTriageState(
                issue_family=scenario['issue_family'],
                confidence=0.78,
                conversation_stage='hypothesis_validation',
                log_sufficiency_status=scenario['log_sufficiency_status'],
                resolution_status=scenario['resolution_status'],
                missing_logs=['vCenter Server logs'],
            ),
            latest_user_feedback=scenario['feedback'],
            has_prior_assistant_turn=True,
        )
        assert decision.next_move == scenario['expected_move']
        assert decision.working_hypothesis.startswith('Working hypothesis:')
        assert decision.focused_question.endswith('?')


def test_vmware_trajectory_regression_harness_kickoff_is_hypothesis_first_and_one_question() -> None:
    assembler = PromptAssembler(transcript_window=6)
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req Kickoff',
            'issue_category': 'Production outage',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'VMs are pausing every few minutes',
            'architecture_notes': '',
            'error_text': '',
            'uploaded_logs_count': 0,
            'uploaded_log_names': [],
            'uploaded_logs_available': False,
            'vmware_triage': {
                'issue_family': 'vm-performance',
                'conversation_stage': 'issue_definition',
                'policy_next_move': 'confirm_scope',
                'log_sufficiency_status': 'insufficient',
                'missing_logs': ['vmkernel.log', 'ESXi host support bundle'],
                'resolution_status': 'blocked_waiting_on_logs',
            },
        }
    }

    messages = assembler.build_messages(transcript=[], user_text='VMs pause for 10 seconds at a time', session_metadata=session_metadata)
    runtime_guidance = next(message.text for message in messages if message.role == 'system' and message.text.startswith('VMware live-session guidance:'))

    assert 'For first response flow: state a working hypothesis, ask for confirmation, then ask one focused next question.' in runtime_guidance
    assert 'Ask one focused next question at a time to move triage forward.' in runtime_guidance
    assert 'If logs were not received, briefly say that' in runtime_guidance
    assert 'Do not use numbered checklists unless the user explicitly asks for one.' in runtime_guidance


def test_session_patch_can_update_expert_desk_metadata_without_renaming(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'Metadata patch'})
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Request: Production outage',
                        'issue_category': 'Production outage',
                        'environment_platform': 'VMware',
                        'urgency': 'Same day',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': 'VMware specialist',
                        'issue_description': 'Host isolation',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                        'recommended_vmware_logs': ['vCenter Server logs', 'vmkernel.log'],
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    assert patched.json()['title'] == 'Metadata patch'
    assert patched.json()['metadata']['expert_desk']['uploaded_logs_available'] is True
    assert transcript.status_code == 200
    assert transcript.json()['session']['metadata']['expert_desk']['uploaded_log_names'] == ['vmkernel.log']


def test_session_patch_vmware_uploaded_logs_immediately_refreshes_log_sufficiency(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware sufficiency refresh',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 1',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnects',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'Recent uplink policy change',
                            'symptom_summary': 'Hosts flap every 20 minutes',
                            'open_questions': ['Do flaps align with vmnic errors?'],
                            'confidence': 0.7,
                            'conversation_stage': 'evidence_gathering',
                            'next_best_question': 'Do vmnic errors align with flap timestamps?',
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-prev',
                        },
                    }
                },
            },
        )
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 1',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnects',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 3,
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log', 'vcenter-events-bundle.tgz'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'Recent uplink policy change',
                            'symptom_summary': 'Hosts flap every 20 minutes',
                            'open_questions': ['Do flaps align with vmnic errors?'],
                            'confidence': 0.7,
                            'conversation_stage': 'evidence_gathering',
                            'next_best_question': 'Do vmnic errors align with flap timestamps?',
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-prev',
                        },
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    assert transcript.status_code == 200
    triage = patched.json()['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == 'host-networking'
    assert triage['suspected_layer'] == 'esxi-network-stack'
    assert triage['confidence'] == 0.7
    assert triage['last_updated_from_turn_id'] == 'turn-prev'
    assert triage['log_sufficiency_status'] == 'sufficient'
    assert triage['required_logs'] == ['vmkernel.log', 'vobd.log', 'vCenter Server logs']
    assert triage['received_logs'] == ['vmkernel.log', 'vobd.log', 'vCenter Server logs']
    assert triage['missing_logs'] == []
    assert triage['optional_logs'] == ['ESXi host support bundle', 'Distributed switch / vmnic event export']
    assert "host-networking" in triage['log_guidance_summary']
    assert triage['policy_next_move'] == 'validate_hypothesis'
    assert triage['conversation_stage'] == 'evidence_gathering'
    assert triage['next_best_question'] == 'Based on your latest details, should we revise the issue family before we continue?'
    transition_events = [event for event in transcript.json()['events'] if event['type'] == 'vmware.trajectory.log_sufficiency_changed']
    assert transition_events
    latest = transition_events[-1]
    assert latest['payload']['previous_value'] == 'partial'
    assert latest['payload']['current_value'] == 'sufficient'
    assert latest['payload']['source_path'] == 'session_patch_refresh'


def test_session_patch_vmware_refresh_preserves_existing_progressed_policy_move(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware preserve progressed move',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 1b',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnects',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 2,
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'Recent uplink policy change',
                            'confidence': 0.8,
                            'conversation_stage': 'verification',
                            'policy_next_move': 'verify_result',
                            'next_best_question': 'Did host health improve?',
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-prev',
                        },
                    }
                },
            },
        )
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 1b',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnects',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 3,
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log', 'vcenter-events-bundle.tgz'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'Recent uplink policy change',
                            'confidence': 0.8,
                            'conversation_stage': 'verification',
                            'policy_next_move': 'verify_result',
                            'next_best_question': 'Did host health improve?',
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-prev',
                        },
                    }
                }
            },
        )

    assert patched.status_code == 200
    triage = patched.json()['metadata']['expert_desk']['vmware_triage']
    assert triage['log_sufficiency_status'] == 'sufficient'
    assert triage['policy_next_move'] == 'verify_result'
    assert triage['conversation_stage'] == 'verification'
    assert triage['next_best_question'] == 'After that step, did alarms, host state, and workload impact improve or stay the same?'


def test_session_patch_non_vmware_persona_does_not_run_vmware_log_sufficiency(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'Non-VMware patch'})
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 2',
                        'issue_category': 'EC2 issue',
                        'environment_platform': 'AWS',
                        'urgency': 'Medium',
                        'preferred_expert_type': 'AI AWS Engineer',
                        'recommended_expert_type': 'AI AWS Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-aws-engineer',
                        'expert_persona_label': 'AI AWS Engineer',
                        'issue_description': 'Instance network timeout',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['ec2-network.log'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'policy_next_move': 'seeded-policy',
                            'conversation_stage': 'seeded-stage',
                            'next_best_question': 'seeded-question',
                            'required_logs': ['seeded-required'],
                            'received_logs': ['seeded-received'],
                            'missing_logs': ['seeded-missing'],
                            'optional_logs': ['seeded-optional'],
                            'log_sufficiency_status': 'seeded-status',
                            'log_guidance_summary': 'seeded-summary',
                        },
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    assert transcript.status_code == 200
    triage = patched.json()['metadata']['expert_desk']['vmware_triage']
    assert triage['required_logs'] == ['seeded-required']
    assert triage['received_logs'] == ['seeded-received']
    assert triage['missing_logs'] == ['seeded-missing']
    assert triage['optional_logs'] == ['seeded-optional']
    assert triage['log_sufficiency_status'] == 'seeded-status'
    assert triage['log_guidance_summary'] == 'seeded-summary'
    assert triage['policy_next_move'] == 'seeded-policy'
    assert triage['conversation_stage'] == 'seeded-stage'
    assert triage['next_best_question'] == 'seeded-question'
    assert [event for event in transcript.json()['events'] if event['type'].startswith('vmware.trajectory.')] == []


def test_session_patch_vmware_transition_events_do_not_duplicate_when_state_unchanged(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware transition dedupe',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req dedupe',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnects',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 3,
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log', 'vcenter-events-bundle.tgz'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'conversation_stage': 'verification',
                            'policy_next_move': 'verify_result',
                            'log_sufficiency_status': 'sufficient',
                            'resolution_status': 'monitoring',
                        },
                    }
                },
            },
        )
        session_id = created.json()['id']
        payload = {
            'metadata': {
                'expert_desk': {
                    'request_label': 'Req dedupe',
                    'issue_category': 'Host disconnect',
                    'environment_platform': 'VMware',
                    'urgency': 'High',
                    'preferred_expert_type': 'AI VMware Engineer',
                    'recommended_expert_type': 'AI VMware Engineer',
                    'recommended_path': 'continue_with_ai_now',
                    'expert_persona_id': 'ai-vmware-engineer',
                    'expert_persona_label': 'AI VMware Engineer',
                    'issue_description': 'Intermittent host disconnects',
                    'architecture_notes': '',
                    'error_text': '',
                    'uploaded_logs_count': 3,
                    'uploaded_log_names': ['vmkernel.log', 'vobd.log', 'vcenter-events-bundle.tgz'],
                    'uploaded_logs_available': True,
                    'vmware_triage': {
                        'issue_family': 'host-networking',
                        'conversation_stage': 'verification',
                        'policy_next_move': 'verify_result',
                        'log_sufficiency_status': 'sufficient',
                        'resolution_status': 'monitoring',
                    },
                }
            }
        }
        first_patch = client.patch(f'/api/v1/sessions/{session_id}', json=payload)
        second_patch = client.patch(f'/api/v1/sessions/{session_id}', json=payload)
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert first_patch.status_code == 200
    assert second_patch.status_code == 200
    assert transcript.status_code == 200
    assert [event for event in transcript.json()['events'] if event['type'].startswith('vmware.trajectory.')] == []


def test_session_patch_vmware_without_issue_family_does_not_invent_triage_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'No triage issue family'})
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 3',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'No triage yet',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 2,
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': '',
                            'policy_next_move': 'seeded-policy',
                            'conversation_stage': 'seeded-stage',
                            'next_best_question': 'seeded-question',
                            'log_sufficiency_status': 'seeded-status',
                            'required_logs': ['seeded-required'],
                            'received_logs': ['seeded-received'],
                            'missing_logs': ['seeded-missing'],
                            'optional_logs': ['seeded-optional'],
                            'log_guidance_summary': 'seeded-summary',
                        },
                    }
                }
            },
        )

    assert patched.status_code == 200
    triage = patched.json()['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == ''
    assert triage['policy_next_move'] == 'seeded-policy'
    assert triage['conversation_stage'] == 'seeded-stage'
    assert triage['next_best_question'] == 'seeded-question'
    assert triage['log_sufficiency_status'] == 'seeded-status'
    assert triage['required_logs'] == ['seeded-required']
    assert triage['received_logs'] == ['seeded-received']
    assert triage['missing_logs'] == ['seeded-missing']
    assert triage['optional_logs'] == ['seeded-optional']
    assert triage['log_guidance_summary'] == 'seeded-summary'


def test_session_patch_metadata_regression_still_persists_general_expert_desk_updates(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'Regression metadata patch'})
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Patch 4',
                        'issue_category': 'General infra support',
                        'environment_platform': 'AWS',
                        'urgency': 'Low',
                        'preferred_expert_type': 'AI AWS Engineer',
                        'recommended_expert_type': 'AI AWS Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-aws-engineer',
                        'expert_persona_label': 'AI AWS Engineer',
                        'issue_description': 'Need architecture guidance',
                        'architecture_notes': 'Staging VPC',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['cloudwatch-export.json'],
                        'uploaded_logs_available': True,
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    assert transcript.status_code == 200
    assert patched.json()['metadata']['expert_desk']['request_label'] == 'Req Patch 4'
    assert transcript.json()['session']['metadata']['expert_desk']['uploaded_log_names'] == ['cloudwatch-export.json']


def test_session_create_persists_typed_vmware_triage_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware triage create',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req 42',
                        'issue_category': 'VM outage',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'launch_live_session_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': 'VMware specialist',
                        'issue_description': 'Multiple guests disconnected',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                        'recommended_vmware_logs': ['vCenter logs'],
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'vDS uplink changed overnight',
                            'confidence': 0.72,
                            'conversation_stage': 'hypothesis_confirmation',
                            'next_best_question': 'Did vmnic link flaps start after the uplink change?',
                            'required_logs': ['vmkernel.log', 'vobd.log'],
                            'received_logs': ['vmkernel.log'],
                            'missing_logs': ['vobd.log'],
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-abc',
                        },
                    }
                },
            },
        )

    assert created.status_code == 201
    triage = created.json()['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == 'host-networking'
    assert triage['confidence'] == 0.72
    assert triage['missing_logs'] == ['vobd.log', 'vCenter Server logs']


def test_session_patch_and_reload_preserves_typed_vmware_triage_state(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post('/api/v1/sessions', json={'title': 'VMware triage patch'})
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req 99',
                        'issue_category': 'Datastore latency',
                        'environment_platform': 'VMware',
                        'urgency': 'Critical',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': 'VMware specialist',
                        'issue_description': 'Latency spikes every 10 minutes',
                        'architecture_notes': 'Shared iSCSI datastore',
                        'error_text': 'APD warnings intermittently',
                        'uploaded_logs_count': 2,
                        'uploaded_log_names': ['vmkernel.log', 'vpxd.log'],
                        'uploaded_logs_available': True,
                        'recommended_vmware_logs': ['vmkernel.log', 'vpxd.log'],
                        'vmware_triage': {
                            'issue_family': 'storage-pathing',
                            'suspected_layer': 'esxi-multipath',
                            'impact_scope': 'multi-host',
                            'recent_change_summary': 'Array firmware updated last night',
                            'confidence': 0.65,
                            'conversation_stage': 'evidence_gathering',
                            'next_best_question': 'Do APD alerts align with SAN controller failovers?',
                            'required_logs': ['vmkernel.log', 'vpxd.log', 'array-event-log'],
                            'received_logs': ['vmkernel.log', 'vpxd.log'],
                            'missing_logs': ['array-event-log'],
                            'resolution_status': 'in_progress',
                            'last_updated_from_turn_id': 'turn-222',
                        },
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    assert transcript.status_code == 200
    triage = transcript.json()['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['suspected_layer'] == 'esxi-multipath'
    assert triage['required_logs'] == ['vmkernel.log', 'ESXi host support bundle', 'Storage array event logs']
    assert triage['missing_logs'] == ['ESXi host support bundle', 'Storage array event logs']


def test_read_expert_desk_metadata_preserves_valid_nested_vmware_triage() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req 111',
            'issue_category': 'Storage outage',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Datastore path flaps',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'issue_family': 'storage-pathing',
                'confidence': 0.5,
                'missing_logs': ['array-event-log'],
            },
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.request_label == 'Req 111'
    assert expert_desk.vmware_triage is not None
    assert expert_desk.vmware_triage.issue_family == 'storage-pathing'
    assert expert_desk.vmware_triage.confidence == 0.5


def test_read_expert_desk_metadata_keeps_none_when_vmware_triage_missing() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req 222',
            'issue_category': 'Host disconnect',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Hosts not responding',
            'architecture_notes': '',
            'error_text': '',
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.request_label == 'Req 222'
    assert expert_desk.vmware_triage is None


def test_read_expert_desk_metadata_preserves_partial_nested_vmware_triage() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req 333',
            'issue_category': 'VM outage',
            'environment_platform': 'VMware',
            'urgency': 'Critical',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Guests disconnected',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'issue_family': 'host-networking',
                'confidence': 2.0,
            },
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.request_label == 'Req 333'
    assert expert_desk.issue_category == 'VM outage'
    assert expert_desk.vmware_triage is not None
    assert expert_desk.vmware_triage.issue_family == 'host-networking'
    assert expert_desk.vmware_triage.confidence == 0.0


def test_prompt_assembler_keeps_expert_desk_preface_when_vmware_triage_partially_invalid() -> None:
    assembler = PromptAssembler(transcript_window=2)
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req 444',
            'issue_category': 'Datastore latency',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Latency spikes',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {'issue_family': 'storage-pathing', 'confidence': -1.0},
        }
    }

    messages = assembler.build_messages(transcript=[], user_text='What do we check first?', session_metadata=session_metadata)

    assert messages[0].role == 'system'
    assert 'You are AskChip Expert Desk' in messages[0].text
    assert 'selected expert persona id: ai-vmware-engineer' in messages[2].text
    assert 'issue category: Datastore latency' in messages[2].text
    assert 'vmware triage issue family: storage-pathing' in messages[2].text
    assert 'vmware triage confidence' not in messages[2].text




def test_read_expert_desk_metadata_preserves_partial_nested_vmware_handoff() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req 335',
            'issue_category': 'VM outage',
            'environment_platform': 'VMware',
            'urgency': 'Critical',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Guests disconnected',
            'architecture_notes': '',
            'error_text': '',
            'vmware_handoff': {
                'issue_summary': 'Intermittent APD',
                'ready_for_handoff': 'not-a-bool',
                'logs_missing': ['array-event.log'],
                'unknown_field': 'ignored',
            },
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.request_label == 'Req 335'
    assert expert_desk.vmware_handoff is not None
    assert expert_desk.vmware_handoff.issue_summary == 'Intermittent APD'
    assert expert_desk.vmware_handoff.logs_missing == ['array-event.log']
    assert expert_desk.vmware_handoff.ready_for_handoff is False


def test_read_expert_desk_metadata_malformed_vmware_handoff_does_not_break_non_vmware_context() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req generic',
            'issue_category': 'General support',
            'environment_platform': 'Linux',
            'urgency': 'Low',
            'preferred_expert_type': 'Generalist',
            'recommended_expert_type': 'Generalist',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-generalist',
            'expert_persona_label': 'AI Generalist',
            'issue_description': 'Need baseline troubleshooting',
            'architecture_notes': '',
            'error_text': '',
            'vmware_handoff': 'invalid-shape',
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.expert_persona_id == 'ai-generalist'
    assert expert_desk.vmware_triage is None
    assert expert_desk.vmware_handoff is None


def test_read_expert_desk_metadata_preserves_valid_vmware_artifacts_when_some_rows_are_invalid() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req artifacts',
            'issue_category': 'VMware outage',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Need logs',
            'architecture_notes': '',
            'error_text': '',
            'vmware_artifacts': [
                {
                    'id': 'a1',
                    'session_id': 's1',
                    'filename': 'vmkernel.log',
                    'content_type': 'text/plain',
                    'size_bytes': 100,
                    'status': 'parsed_supported',
                    'artifact_type': 'vmkernel.log',
                    'uploaded_at': '2026-03-10T00:00:00',
                    'storage_path': '/tmp/a1',
                    'evidence': {
                        'parser_kind': 'vmware_log_v1',
                        'artifact_type': 'vmkernel.log',
                        'parsed_line_count': 3,
                        'matched_categories': ['storage'],
                        'notable_lines': ['error line'],
                        'parse_warnings': [],
                    },
                },
                {'id': 'broken-row', 'filename': 'missing-required-fields.log'},
            ],
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert len(expert_desk.vmware_artifacts) == 1
    assert expert_desk.vmware_artifacts[0].filename == 'vmkernel.log'


def test_read_expert_desk_metadata_vmware_artifact_invalid_evidence_is_safely_dropped() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req artifacts 2',
            'issue_category': 'VMware outage',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Need logs',
            'architecture_notes': '',
            'error_text': '',
            'vmware_artifacts': [
                {
                    'id': 'a2',
                    'session_id': 's2',
                    'filename': 'vobd.log',
                    'content_type': 'text/plain',
                    'size_bytes': 200,
                    'status': 'parsed_supported',
                    'artifact_type': 'vobd.log',
                    'uploaded_at': '2026-03-11T00:00:00',
                    'storage_path': '/tmp/a2',
                    'evidence': 'invalid',
                }
            ],
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert len(expert_desk.vmware_artifacts) == 1
    assert expert_desk.vmware_artifacts[0].filename == 'vobd.log'
    assert expert_desk.vmware_artifacts[0].evidence is None


def test_read_expert_desk_metadata_drops_unusable_nested_vmware_triage() -> None:
    metadata = {
        'expert_desk': {
            'request_label': 'Req 334',
            'issue_category': 'VM outage',
            'environment_platform': 'VMware',
            'urgency': 'Critical',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'launch_live_session_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Guests disconnected',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {
                'confidence': 2.0,
            },
        }
    }

    expert_desk = read_expert_desk_metadata(metadata)

    assert expert_desk is not None
    assert expert_desk.request_label == 'Req 334'
    assert expert_desk.vmware_triage is None


def test_update_vmware_triage_state_overwrites_invalid_nested_vmware_triage() -> None:
    session_metadata = {
        'expert_desk': {
            'request_label': 'Req 555',
            'issue_category': 'Network flap',
            'environment_platform': 'VMware',
            'urgency': 'High',
            'preferred_expert_type': 'AI VMware Engineer',
            'recommended_expert_type': 'AI VMware Engineer',
            'recommended_path': 'continue_with_ai_now',
            'expert_persona_id': 'ai-vmware-engineer',
            'expert_persona_label': 'AI VMware Engineer',
            'issue_description': 'Frequent vmnic down events',
            'architecture_notes': '',
            'error_text': '',
            'vmware_triage': {'confidence': 9.9},
        }
    }
    updated = update_vmware_triage_state(
        session_metadata,
        VmwareTriageState(issue_family='host-networking', confidence=0.61, missing_logs=['vobd.log']),
    )

    assert updated['expert_desk']['request_label'] == 'Req 555'
    triage = updated['expert_desk']['vmware_triage']
    assert isinstance(triage, dict)
    assert triage['issue_family'] == 'host-networking'
    assert triage['confidence'] == 0.61
    assert triage['missing_logs'] == ['vobd.log']
    assert triage['resolution_status'] == 'unresolved'
    assert updated['expert_desk']['vmware_handoff']['current_resolution_status'] == 'unresolved'


def test_normalize_vmware_resolution_status_aliases() -> None:
    assert normalize_vmware_resolution_status('in_progress') == 'unresolved'
    assert normalize_vmware_resolution_status('waiting_on_logs') == 'blocked_waiting_on_logs'
    assert normalize_vmware_resolution_status('waiting_on_user') == 'blocked_waiting_on_user_action'
    assert normalize_vmware_resolution_status('stable') == 'monitoring'
    assert normalize_vmware_resolution_status('handoff_required') == 'needs_human_handoff'




def test_vmware_handoff_packet_humanizes_policy_next_move_when_question_missing() -> None:
    expert_desk = read_expert_desk_metadata(
        {
            'expert_desk': {
                'request_label': 'Req H-humanized',
                'issue_category': 'Storage APD',
                'environment_platform': 'VMware',
                'urgency': 'High',
                'preferred_expert_type': 'AI VMware Engineer',
                'recommended_expert_type': 'AI VMware Engineer',
                'recommended_path': 'continue_with_ai_now',
                'expert_persona_id': 'ai-vmware-engineer',
                'expert_persona_label': 'AI VMware Engineer',
                'issue_description': 'APD on datastore',
                'architecture_notes': '',
                'error_text': '',
                'vmware_triage': {
                    'issue_family': 'storage-pathing',
                    'policy_next_move': 'request_missing_logs',
                    'missing_logs': ['ESXi host support bundle'],
                    'next_best_question': '',
                },
            }
        }
    )
    assert expert_desk is not None

    packet = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=None)

    assert packet is not None
    assert packet.recommended_next_step
    assert packet.recommended_next_step != 'request_missing_logs'
    assert 'request_missing_logs' not in packet.recommended_next_step
    assert 'ESXi host support bundle' in packet.recommended_next_step

def test_vmware_handoff_packet_reflects_blocked_waiting_on_logs_state() -> None:
    expert_desk = read_expert_desk_metadata(
        {
            'expert_desk': {
                'request_label': 'Req H-logs',
                'issue_category': 'Storage APD',
                'environment_platform': 'VMware',
                'urgency': 'High',
                'preferred_expert_type': 'AI VMware Engineer',
                'recommended_expert_type': 'AI VMware Engineer',
                'recommended_path': 'continue_with_ai_now',
                'expert_persona_id': 'ai-vmware-engineer',
                'expert_persona_label': 'AI VMware Engineer',
                'issue_description': 'APD on datastore',
                'architecture_notes': '',
                'error_text': '',
                'uploaded_log_names': ['vmkernel.log'],
                'vmware_triage': {
                    'issue_family': 'storage-pathing',
                    'symptom_summary': 'APD still active',
                    'log_sufficiency_status': 'insufficient',
                    'received_logs': ['vmkernel.log'],
                    'missing_logs': ['ESXi host support bundle'],
                    'resolution_status': 'waiting_on_logs',
                    'next_best_question': 'Can you upload ESXi host support bundle next?',
                },
            }
        }
    )
    assert expert_desk is not None
    packet = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=[{'role': 'user', 'text': 'APD started'}])
    assert packet is not None
    assert packet.current_resolution_status == 'blocked_waiting_on_logs'
    assert packet.logs_received == ['vmkernel.log']
    assert packet.logs_missing == ['ESXi host support bundle']
    assert packet.ready_for_handoff is False


def test_vmware_handoff_packet_reflects_blocked_waiting_on_user_action_state() -> None:
    expert_desk = read_expert_desk_metadata(
        {
            'expert_desk': {
                'request_label': 'Req H-user',
                'issue_category': 'Host networking',
                'environment_platform': 'VMware',
                'urgency': 'High',
                'preferred_expert_type': 'AI VMware Engineer',
                'recommended_expert_type': 'AI VMware Engineer',
                'recommended_path': 'continue_with_ai_now',
                'expert_persona_id': 'ai-vmware-engineer',
                'expert_persona_label': 'AI VMware Engineer',
                'issue_description': 'Hosts disconnected',
                'architecture_notes': '',
                'error_text': '',
                'vmware_triage': {
                    'issue_family': 'host-networking',
                    'resolution_status': 'blocked_waiting_on_user_action',
                    'log_sufficiency_status': 'sufficient',
                    'next_best_question': 'Can you run the NIC health check on host-03?',
                },
            }
        }
    )
    assert expert_desk is not None
    packet = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=None)
    assert packet is not None
    assert packet.current_resolution_status == 'blocked_waiting_on_user_action'
    assert 'user action' in packet.handoff_reason.lower()


def test_vmware_handoff_packet_reflects_resolved_and_handoff_states() -> None:
    resolved = read_expert_desk_metadata(
        {
            'expert_desk': {
                'request_label': 'Req resolved',
                'issue_category': 'VM perf',
                'environment_platform': 'VMware',
                'urgency': 'Same day',
                'preferred_expert_type': 'AI VMware Engineer',
                'recommended_expert_type': 'AI VMware Engineer',
                'recommended_path': 'continue_with_ai_now',
                'expert_persona_id': 'ai-vmware-engineer',
                'expert_persona_label': 'AI VMware Engineer',
                'issue_description': 'CPU ready high',
                'architecture_notes': '',
                'error_text': '',
                'vmware_triage': {'issue_family': 'vm-performance', 'resolution_status': 'resolved'},
            }
        }
    )
    handoff = read_expert_desk_metadata(
        {
            'expert_desk': {
                'request_label': 'Req handoff',
                'issue_category': 'vCenter',
                'environment_platform': 'VMware',
                'urgency': 'Critical',
                'preferred_expert_type': 'AI VMware Engineer',
                'recommended_expert_type': 'AI VMware Engineer',
                'recommended_path': 'escalate_to_human_expert',
                'expert_persona_id': 'ai-vmware-engineer',
                'expert_persona_label': 'AI VMware Engineer',
                'issue_description': 'vCenter flapping',
                'architecture_notes': '',
                'error_text': '',
                'vmware_triage': {'issue_family': 'vcenter-services', 'resolution_status': 'needs_human_handoff'},
            }
        }
    )
    assert resolved is not None
    assert handoff is not None
    resolved_packet = build_vmware_handoff_packet(expert_desk=resolved, transcript_messages=None)
    handoff_packet = build_vmware_handoff_packet(expert_desk=handoff, transcript_messages=None)
    assert resolved_packet is not None
    assert handoff_packet is not None
    assert resolved_packet.current_resolution_status == 'resolved'
    assert resolved_packet.ready_for_handoff is False
    assert handoff_packet.current_resolution_status == 'needs_human_handoff'
    assert handoff_packet.ready_for_handoff is True


def test_session_patch_refresh_persists_vmware_handoff_packet_from_current_triage_state(tmp_path: Path) -> None:
    app = make_app(tmp_path, transport=streaming_transport([{'message': {'content': 'ok'}, 'done': True}]))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware handoff refresh',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req HP-1',
                        'issue_category': 'Host network',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent host disconnect',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_log_names': ['vmkernel.log'],
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'recent_change_summary': 'vDS change last night',
                            'symptom_summary': 'Hosts disconnected twice this morning',
                            'open_questions': ['Did this start right after change window?'],
                            'conversation_stage': 'issue_definition',
                            'policy_next_move': 'request_missing_logs',
                            'next_best_question': 'Can you upload vobd.log next?',
                            'required_logs': ['vmkernel.log', 'vobd.log', 'vCenter Server logs'],
                            'received_logs': ['vmkernel.log'],
                            'missing_logs': ['vobd.log', 'vCenter Server logs'],
                            'log_sufficiency_status': 'partial',
                            'resolution_status': 'in_progress',
                        },
                    }
                },
            },
        )
        session_id = created.json()['id']
        patched = client.patch(
            f'/api/v1/sessions/{session_id}',
            json={
                'metadata': {
                    'expert_desk': {
                        **created.json()['metadata']['expert_desk'],
                        'uploaded_log_names': ['vmkernel.log', 'vobd.log'],
                    }
                }
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert patched.status_code == 200
    handoff = transcript.json()['session']['metadata']['expert_desk']['vmware_handoff']
    assert handoff['current_resolution_status'] == 'unresolved'
    assert handoff['logs_received'] == ['vmkernel.log', 'vobd.log']
    assert handoff['logs_missing'] == ['vCenter Server logs']
    assert handoff['log_sufficiency_status'] in {'partial', 'sufficient'}
    assert handoff['recommended_next_step']


def test_vmware_typed_turn_updates_triage_state_from_hidden_extraction(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        calls.append(payload)
        if len(calls) == 1:
            extraction = {
                'issue_family': 'host-networking',
                'suspected_layer': 'esxi-network-stack',
                'impact_scope': 'single-cluster',
                'recent_change_summary': 'vDS uplink policy changed last night',
                'symptom_summary': 'Hosts intermittently disconnect and VMs lose connectivity',
                'open_questions': ['Did vmnic flaps start right after the vDS change?'],
                'confidence': 0.81,
                'recommended_conversation_stage': 'hypothesis_confirmation',
                'required_logs': ['vmkernel.log', 'vobd.log'],
                'received_logs': ['vmkernel.log'],
                'missing_logs': ['vobd.log'],
                'resolution_status': 'in_progress',
            }
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(extraction)}, 'done': True}).encode() + b'\n')
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Let us verify host uplink state first.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware extraction typed',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req T-1',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Hosts disconnected',
                        'architecture_notes': '',
                        'error_text': '',
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hosts dropped after last-night vDS changes.'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert turn.status_code == 201
    assert transcript.status_code == 200
    triage = transcript.json()['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == 'host-networking'
    assert triage['policy_next_move'] == 'request_missing_logs'
    assert triage['conversation_stage'] == 'log_collection'
    assert triage['open_questions'] == ['Did vmnic flaps start right after the vDS change?']
    assert triage['next_best_question'] == 'Can you upload vmkernel.log, vobd.log, vCenter Server logs next so we can validate this path?'
    assert triage['log_sufficiency_status'] == 'insufficient'
    assert triage['required_logs'] == ['vmkernel.log', 'vobd.log', 'vCenter Server logs']
    assert triage['received_logs'] == []
    assert triage['missing_logs'] == ['vmkernel.log', 'vobd.log', 'vCenter Server logs']
    assert triage['optional_logs'] == ['ESXi host support bundle', 'Distributed switch / vmnic event export']
    assert 'metadata' in triage['log_guidance_summary'].lower() or 'collect' in triage['log_guidance_summary'].lower()
    assert len(calls) == 2


def test_vmware_turn_trajectory_updates_policy_after_user_correction(tmp_path: Path) -> None:
    assistant_payloads: list[dict[str, object]] = []
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        payload = json.loads(request.content.decode())
        calls += 1
        if calls == 1:
            extraction = {
                'issue_family': 'host-networking',
                'suspected_layer': 'esxi-network-stack',
                'impact_scope': '',
                'recent_change_summary': 'vDS uplink policy changed',
                'symptom_summary': 'host disconnect alarms',
                'open_questions': ['Is impact single cluster or multiple clusters?'],
                'confidence': 0.79,
                'recommended_conversation_stage': 'hypothesis_confirmation',
                'required_logs': ['vmkernel.log', 'vobd.log'],
                'received_logs': ['vmkernel.log'],
                'missing_logs': ['vobd.log'],
                'resolution_status': 'in_progress',
            }
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(extraction)}, 'done': True}).encode() + b'\n')
        if calls == 2:
            assistant_payloads.append(payload)
            return httpx.Response(200, content=json.dumps({'message': {'content': 'Understood. Let us confirm scope first.'}, 'done': True}).encode() + b'\n')
        if calls == 3:
            extraction = {
                'issue_family': 'storage-pathing',
                'suspected_layer': 'esxi-multipath',
                'impact_scope': 'single-cluster',
                'recent_change_summary': 'no recent storage changes',
                'symptom_summary': 'APD on one datastore',
                'open_questions': ['Do APD events align with array controller alerts?'],
                'confidence': 0.76,
                'recommended_conversation_stage': 'evidence_gathering',
                'required_logs': ['vmkernel.log', 'ESXi host support bundle', 'Storage array event logs'],
                'received_logs': ['vmkernel.log'],
                'missing_logs': ['ESXi host support bundle', 'Storage array event logs'],
                'resolution_status': 'in_progress',
            }
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(extraction)}, 'done': True}).encode() + b'\n')
        assistant_payloads.append(payload)
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Thanks for the correction. I will revise the hypothesis.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware trajectory policy',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req Traj-1',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Host disconnect alarms',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                    }
                },
            },
        )
        session_id = created.json()['id']
        first_turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Hosts started disconnecting after last night change.'})
        second_turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'No, correction: this is storage APD and not network.'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert first_turn.status_code == 201
    assert second_turn.status_code == 201
    assert transcript.status_code == 200
    assert len(assistant_payloads) == 2
    first_system_messages = [message['content'] for message in assistant_payloads[0]['messages'] if message['role'] == 'system']
    second_system_messages = [message['content'] for message in assistant_payloads[1]['messages'] if message['role'] == 'system']
    first_policy = next(message for message in first_system_messages if message.startswith('VMware live-session guidance:'))
    second_policy = next(message for message in second_system_messages if message.startswith('VMware live-session guidance:'))
    assert 'Deterministic policy next move: confirm_scope.' in first_policy
    assert 'Deterministic policy working hypothesis:' in first_policy
    assert 'Deterministic policy focused next question: Is the impact isolated' in first_policy
    assert 'Deterministic policy next move: validate_hypothesis.' in second_policy
    assert 'If the user corrects your path, explicitly revise your working hypothesis before proposing the next step.' in second_policy
    triage = transcript.json()['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['policy_next_move'] == 'validate_hypothesis'
    assert triage['conversation_stage'] == 'hypothesis_validation'
    assert triage['next_best_question'] == 'Based on your latest details, should we revise the issue family before we continue?'


def test_vmware_partial_log_sufficiency_preserves_missing_log_guidance_and_policy_question(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content.decode())
        messages = payload.get('messages', [])
        is_extraction_call = bool(messages) and 'triage extraction engine' in messages[0].get('content', '').lower()
        if is_extraction_call:
            extraction = {
                'issue_family': 'storage-pathing',
                'suspected_layer': 'esxi-multipath',
                'impact_scope': 'single-cluster',
                'recent_change_summary': 'SAN maintenance completed',
                'symptom_summary': 'APD events on one datastore',
                'open_questions': ['Do APD alarms align with storage controller events?'],
                'confidence': 0.8,
                'recommended_conversation_stage': 'evidence_gathering',
                'required_logs': ['vmkernel.log', 'ESXi host support bundle', 'Storage array event logs'],
                'received_logs': ['vmkernel.log'],
                'missing_logs': ['ESXi host support bundle', 'Storage array event logs'],
                'resolution_status': 'in_progress',
            }
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(extraction)}, 'done': True}).encode() + b'\n')
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Please upload the remaining storage logs so we can continue.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware partial logs',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req P-1',
                        'issue_category': 'Storage APD',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Datastore APD alarms',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'APD started after SAN firmware update.'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert turn.status_code == 201
    assert transcript.status_code == 200
    triage = transcript.json()['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['log_sufficiency_status'] == 'partial'
    assert triage['missing_logs'] == ['ESXi host support bundle', 'Storage array event logs']
    assert triage['policy_next_move'] == 'confirm_issue_family'
    assert triage['conversation_stage'] == 'issue_definition'
    assert triage['next_best_question'] == 'Does this align most with host networking, storage pathing, vCenter services, or VM performance impact?'
    assert 'more are still needed: ESXi host support bundle, Storage array event logs' in triage['log_guidance_summary']


def test_non_vmware_sessions_do_not_persist_vmware_policy_triage_fields(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Checking AWS health now.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'AWS session',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req AWS-1',
                        'issue_category': 'EC2 outage',
                        'environment_platform': 'AWS',
                        'urgency': 'Critical',
                        'preferred_expert_type': 'AI AWS Engineer',
                        'recommended_expert_type': 'AI AWS Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-aws-engineer',
                        'expert_persona_label': 'AI AWS Engineer',
                        'issue_description': 'Instances unreachable',
                        'architecture_notes': '',
                        'error_text': '',
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Can you help me triage this outage?'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert turn.status_code == 201
    assert transcript.status_code == 200
    assert transcript.json()['session']['metadata']['expert_desk'].get('vmware_triage') is None
    assert transcript.json()['session']['metadata']['expert_desk'].get('vmware_handoff') is None


def test_vmware_voice_turn_updates_same_triage_state(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        calls.append(payload)
        if len(calls) == 1:
            extraction = {
                'issue_family': 'storage-pathing',
                'suspected_layer': 'esxi-multipath',
                'impact_scope': 'multi-host',
                'recent_change_summary': 'SAN firmware upgraded this weekend',
                'symptom_summary': 'Datastores show APD on several hosts',
                'open_questions': ['Do APD timestamps align with SAN controller failover events?'],
                'confidence': 0.78,
                'recommended_conversation_stage': 'evidence_gathering',
                'required_logs': ['vmkernel.log', 'vpxd.log', 'array-event-log'],
                'received_logs': ['vmkernel.log'],
                'missing_logs': ['vpxd.log', 'array-event-log'],
                'resolution_status': 'in_progress',
            }
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(extraction)}, 'done': True}).encode() + b'\n')
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Please gather APD timeline and SAN failover events.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler), stt_service=FakeSttService(text='APD started after SAN update'))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware extraction voice',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req V-1',
                        'issue_category': 'Datastore latency',
                        'environment_platform': 'VMware',
                        'urgency': 'Critical',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'launch_live_session_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Intermittent APD',
                        'architecture_notes': '',
                        'error_text': '',
                    }
                },
            },
        )
        session_id = created.json()['id']
        start = client.post(f'/api/v1/sessions/{session_id}/voice-turns/ptt/start')
        voice = client.post(
            f'/api/v1/sessions/{session_id}/voice-turns?filename=voice-turn.webm',
            content=b'voice-bytes',
            headers={'content-type': 'audio/webm'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert start.status_code == 200
    assert voice.status_code == 201
    triage = transcript.json()['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == 'storage-pathing'
    assert triage['conversation_stage'] == 'log_collection'
    assert triage['log_sufficiency_status'] == 'insufficient'
    assert triage['required_logs'] == ['vmkernel.log', 'ESXi host support bundle', 'Storage array event logs']
    assert triage['received_logs'] == []
    assert triage['missing_logs'] == ['vmkernel.log', 'ESXi host support bundle', 'Storage array event logs']
    assert triage['optional_logs'] == ['vpxd.log', 'HBA driver logs']
    assert len(calls) == 2


def test_invalid_vmware_extraction_does_not_overwrite_existing_triage_state(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            invalid_extraction = {'issue_family': 'host-networking', 'confidence': 2.4}
            return httpx.Response(200, content=json.dumps({'message': {'content': json.dumps(invalid_extraction)}, 'done': True}).encode() + b'\n')
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Can you confirm exact VM impact scope?'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'VMware extraction invalid',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Req I-1',
                        'issue_category': 'Host networking',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'issue_description': 'Hosts disconnecting',
                        'architecture_notes': '',
                        'error_text': '',
                        'vmware_triage': {
                            'issue_family': 'host-networking',
                            'suspected_layer': 'esxi-network-stack',
                            'impact_scope': 'single-cluster',
                            'confidence': 0.66,
                            'conversation_stage': 'evidence_gathering',
                            'missing_logs': ['vobd.log'],
                            'last_updated_from_turn_id': 'turn-prev',
                        },
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Any update?'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert turn.status_code == 201
    triage = transcript['session']['metadata']['expert_desk']['vmware_triage']
    assert triage['issue_family'] == 'host-networking'
    assert triage['confidence'] == 0.66
    assert triage['last_updated_from_turn_id'] == 'turn-prev'
    triage_events = [event for event in transcript['events'] if event['type'] == 'vmware.triage.skipped']
    assert triage_events


def test_legacy_session_without_vmware_triage_state_still_supports_turns(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'Let us check host and datastore health first.'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        created = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Legacy expert metadata',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'Legacy 1',
                        'issue_category': 'Host disconnect',
                        'environment_platform': 'VMware',
                        'urgency': 'High',
                        'preferred_expert_type': 'AI VMware Engineer',
                        'recommended_expert_type': 'AI VMware Engineer',
                        'recommended_path': 'continue_with_ai_now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': 'VMware specialist',
                        'issue_description': 'Hosts enter not responding state',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 0,
                        'uploaded_log_names': [],
                        'uploaded_logs_available': False,
                        'recommended_vmware_logs': ['vmkernel.log'],
                    }
                },
            },
        )
        session_id = created.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'Can you guide next steps?'})
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript')

    assert turn.status_code == 201
    assert transcript.status_code == 200
    assert transcript.json()['session']['metadata']['expert_desk'].get('vmware_triage') is None
    messages = transcript.json()['messages']
    assert messages[0]['text'] == 'Can you guide next steps?'
    assert 'content' not in messages[0]
    payload = captured['payload']
    assert isinstance(payload, dict)


def test_prompt_assembler_uses_general_expert_fallback_overlay() -> None:
    assembler = PromptAssembler()
    messages = assembler.build_messages(
        transcript=[],
        user_text='help',
        session_metadata={'expert_desk': {'expert_persona_label': 'AI Quantum Datacenter Wizard'}},
    )

    assert messages[1].role == 'system'
    assert 'General Infrastructure Expert Engineer' in messages[1].text


def test_prompt_assembler_legacy_persona_label_fallback_still_works() -> None:
    assembler = PromptAssembler()
    messages = assembler.build_messages(
        transcript=[],
        user_text='help',
        session_metadata={'expert_desk': {'expert_persona': 'AI AWS Engineer'}},
    )

    assert messages[1].role == 'system'
    assert 'AI AWS Engineer' in messages[1].text


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
    monkeypatch.setenv('ASKCHIP_TTS_VOICE', 'af_heart')

    config = load_settings()

    assert config.host == '0.0.0.0'
    assert config.port == 9000
    assert config.database_path == Path('/tmp/askchip.db')
    assert config.prompt_transcript_window == 4
    assert config.ollama_model == 'custom:model'
    assert config.ollama_num_parallel == 1
    assert config.tts_voice == 'af_heart'


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



def test_tts_sanitization_lightens_semicolons_and_colons_for_speech_only(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(
        db,
        status='streaming',
        text='First: check this; then keep going.',
    )

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['First, check this, then keep going.']
    assert stored.text == 'First: check this; then keep going.'


def test_tts_sanitization_smooths_parenthetical_asides_for_speech_only(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(
        db,
        status='streaming',
        text="That plan is workable (honestly), and it'll save time.",
    )

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ["That plan is workable, honestly, and it'll save time."]
    assert stored.text == "That plan is workable (honestly), and it'll save time."


def test_tts_sanitization_flattens_multiline_listy_text_for_speech_only(tmp_path: Path) -> None:
    tts = FakeTtsService()
    db, speech = make_speech_service(tmp_path, tts_adapter=tts)
    session, message = seed_session_with_assistant_message(
        db,
        status='streaming',
        text='Here is the plan\n- prep the field\n- water lightly\n1. gather tools',
    )

    speech.synthesize_message(session.id, message.id, text=message.text)
    stored = db.list_messages(session.id)[-1]

    assert tts.calls == ['Here is the plan, prep the field, water lightly, gather tools']
    assert stored.text == 'Here is the plan\n- prep the field\n- water lightly\n1. gather tools'


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
    assert body['runtime']['tts']['requested_device'] == 'auto'
    assert body['runtime']['tts']['selected_device'] in {'cpu', 'cuda'}
    assert isinstance(body['runtime']['tts']['available_providers'], list)
    if body['runtime']['tts']['selected_device'] == 'cpu':
        assert body['runtime']['tts']['provider'] in {'CPUExecutionProvider', 'unknown'}
    else:
        assert body['runtime']['tts']['provider'] == 'CUDAExecutionProvider'
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


def create_vmware_expert_desk_session(client: TestClient, *, title: str = 'Artifacts') -> str:
    return client.post(
        '/api/v1/sessions',
        json={
            'title': title,
            'metadata': {
                'expert_desk': {
                    'request_label': 'req',
                    'issue_category': 'production-outage',
                    'environment_platform': 'vmware',
                    'urgency': 'same-day',
                    'preferred_expert_type': 'ai-vmware-engineer',
                    'recommended_expert_type': 'ai-vmware-engineer',
                    'recommended_path': 'launch-live-expert-now',
                    'expert_persona_id': 'ai-vmware-engineer',
                    'expert_persona_label': 'AI VMware Engineer',
                    'expert_persona_summary': '',
                    'issue_description': 'Datastore issue',
                    'architecture_notes': '',
                    'error_text': '',
                    'uploaded_logs_count': 0,
                    'uploaded_log_names': [],
                    'uploaded_logs_available': False,
                    'vmware_triage': {
                        'issue_family': 'vcenter-services',
                        'conversation_stage': 'issue_definition',
                        'policy_next_move': 'request_missing_logs',
                    },
                },
            },
        },
    ).json()['id']


def test_session_artifact_upload_rejects_empty_body_without_mutation(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = create_vmware_expert_desk_session(client, title='Empty upload')
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'',
            headers={'X-Artifact-Filename': 'vmkernel.log', 'Content-Type': 'text/plain'},
        )
        listed = client.get(f'/api/v1/sessions/{session_id}/artifacts')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 422
    assert response.json()['detail'] == 'artifact upload body must not be empty'
    assert listed.json()['items'] == []
    assert transcript['session']['metadata']['expert_desk']['uploaded_logs_count'] == 0
    assert transcript['session']['metadata']['expert_desk']['uploaded_log_names'] == []
    assert transcript['session']['metadata']['expert_desk']['uploaded_logs_available'] is False
    assert [event for event in transcript['events'] if event['type'].startswith('vmware.trajectory.')] == []
    artifacts_dir = tmp_path / 'artifacts' / session_id
    assert not artifacts_dir.exists()


def test_session_artifact_upload_rejects_oversized_body_without_mutation(tmp_path: Path) -> None:
    app = make_app(tmp_path, max_artifact_upload_bytes=32)
    with TestClient(app) as client:
        session_id = create_vmware_expert_desk_session(client, title='Oversized upload')
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'x' * 33,
            headers={'X-Artifact-Filename': 'vpxd.log', 'Content-Type': 'text/plain'},
        )
        listed = client.get(f'/api/v1/sessions/{session_id}/artifacts')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 413
    assert response.json()['detail'] == 'artifact upload exceeds maximum size of 32 bytes'
    assert listed.json()['items'] == []
    assert transcript['session']['metadata']['expert_desk']['uploaded_logs_count'] == 0
    assert transcript['session']['metadata']['expert_desk']['uploaded_log_names'] == []
    assert transcript['session']['metadata']['expert_desk']['uploaded_logs_available'] is False
    assert [event for event in transcript['events'] if event['type'].startswith('vmware.trajectory.')] == []
    artifacts_dir = tmp_path / 'artifacts' / session_id
    assert not artifacts_dir.exists()


def test_session_artifact_upload_is_backend_authoritative_without_session_patch(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = create_vmware_expert_desk_session(client)
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 ERROR vmfs datastore issue',
            headers={'X-Artifact-Filename': 'vpxd.log', 'Content-Type': 'text/plain'},
        )
        listed = client.get(f'/api/v1/sessions/{session_id}/artifacts')
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 201
    artifact = response.json()['artifact']
    assert artifact['status'] == 'parsed_supported'
    assert artifact['evidence']['parsed_line_count'] == 1
    assert listed.status_code == 200
    assert len(listed.json()['items']) == 1
    assert 'text' in transcript['messages'][0] if transcript['messages'] else True
    assert transcript['session']['status'] in CONTRACT_TRANSCRIPT_STATES
    expert_desk = transcript['session']['metadata']['expert_desk']
    assert expert_desk['uploaded_logs_count'] == 1
    assert expert_desk['uploaded_log_names'] == ['vpxd.log']
    assert expert_desk['uploaded_logs_available'] is True
    assert expert_desk['vmware_triage']['log_sufficiency_status'] in {'partial', 'sufficient', 'insufficient', 'unknown_issue_family'}
    assert expert_desk['vmware_triage']['policy_next_move'] in {
        'confirm_issue_family',
        'confirm_scope',
        'collect_recent_change',
        'request_missing_logs',
        'validate_hypothesis',
        'propose_safe_next_step',
        'verify_result',
        'summarize_progress',
        'handoff_required',
        'resolution_confirmed',
    }
    transition_events = [event for event in transcript['events'] if event['type'].startswith('vmware.trajectory.')]
    assert transition_events
    assert all(event['payload']['source_path'] == 'artifact_upload_refresh' for event in transition_events)
    assert all('trace_id' not in event['payload'] for event in transition_events)


def test_session_artifact_upload_with_trace_id_header_includes_trace_id_on_transition_events(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Artifacts traced',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'req',
                        'issue_category': 'production-outage',
                        'environment_platform': 'vmware',
                        'urgency': 'same-day',
                        'preferred_expert_type': 'ai-vmware-engineer',
                        'recommended_expert_type': 'ai-vmware-engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': '',
                        'issue_description': 'Datastore issue',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 0,
                        'uploaded_log_names': [],
                        'uploaded_logs_available': False,
                        'vmware_triage': {
                            'issue_family': 'vcenter-services',
                            'conversation_stage': 'issue_definition',
                            'policy_next_move': 'request_missing_logs',
                        },
                    },
                },
            },
        ).json()['id']
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 ERROR vmfs datastore issue',
            headers={
                'X-Artifact-Filename': 'vpxd.log',
                'Content-Type': 'text/plain',
                'X-AskChip-Trace-Id': 'upload-trace-123',
            },
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 201
    transition_events = [event for event in transcript['events'] if event['type'].startswith('vmware.trajectory.')]
    assert transition_events
    artifact_refresh_events = [
        event for event in transition_events
        if event['payload'].get('source_path') == 'artifact_upload_refresh'
    ]
    assert artifact_refresh_events
    assert all(event['payload'].get('trace_id') == 'upload-trace-123' for event in artifact_refresh_events)


def test_session_artifact_upload_marks_unsupported(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Artifacts',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'req',
                        'issue_category': 'production-outage',
                        'environment_platform': 'vmware',
                        'urgency': 'same-day',
                        'preferred_expert_type': 'ai-vmware-engineer',
                        'recommended_expert_type': 'ai-vmware-engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': '',
                        'issue_description': 'Datastore issue',
                        'architecture_notes': '',
                        'error_text': '',
                    },
                },
            },
        ).json()['id']
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 hostd started',
            headers={'X-Artifact-Filename': 'hostd.log', 'Content-Type': 'text/plain'},
        )

    assert response.status_code == 201
    assert response.json()['artifact']['status'] == 'uploaded_unsupported'


def test_session_artifact_upload_parse_failure_is_explicit(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post(
            '/api/v1/sessions',
            json={
                'title': 'Artifacts',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'req',
                        'issue_category': 'production-outage',
                        'environment_platform': 'vmware',
                        'urgency': 'same-day',
                        'preferred_expert_type': 'ai-vmware-engineer',
                        'recommended_expert_type': 'ai-vmware-engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': '',
                        'issue_description': 'Datastore issue',
                        'architecture_notes': '',
                        'error_text': '',
                    },
                },
            },
        ).json()['id']
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'\xff\xfe\x00\x00',
            headers={'X-Artifact-Filename': 'vpxd.log', 'Content-Type': 'text/plain'},
        )

    assert response.status_code == 201
    artifact = response.json()['artifact']
    assert artifact['status'] == 'parse_failed'
    assert artifact['parse_error']


def test_session_artifact_upload_rejects_non_vmware_expert_desk_sessions(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post('/api/v1/sessions', json={'title': 'No vmware metadata'}).json()['id']
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 ERROR vmfs datastore issue',
            headers={'X-Artifact-Filename': 'vmkernel.log', 'Content-Type': 'text/plain'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()
        listed = client.get(f'/api/v1/sessions/{session_id}/artifacts')

    assert response.status_code == 409
    assert 'only supported for VMware Expert Desk sessions' in response.json()['detail']
    assert transcript['session']['metadata'] == {}
    assert listed.json()['items'] == []


def test_session_artifact_upload_emits_no_trajectory_events_when_state_unchanged(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post(
            '/api/v1/sessions',
            json={
                'title': 'No state change',
                'metadata': {
                    'expert_desk': {
                        'request_label': 'req',
                        'issue_category': 'production-outage',
                        'environment_platform': 'vmware',
                        'urgency': 'same-day',
                        'preferred_expert_type': 'ai-vmware-engineer',
                        'recommended_expert_type': 'ai-vmware-engineer',
                        'recommended_path': 'launch-live-expert-now',
                        'expert_persona_id': 'ai-vmware-engineer',
                        'expert_persona_label': 'AI VMware Engineer',
                        'expert_persona_summary': '',
                        'issue_description': 'Datastore issue',
                        'architecture_notes': '',
                        'error_text': '',
                        'uploaded_logs_count': 1,
                        'uploaded_log_names': ['vmkernel.log'],
                        'uploaded_logs_available': True,
                        'vmware_triage': {
                            'issue_family': 'storage_latency',
                            'log_sufficiency_status': 'partial',
                            'required_logs': ['vmkernel.log', 'vpxd.log'],
                            'received_logs': ['vmkernel.log'],
                            'missing_logs': ['vpxd.log'],
                            'policy_next_move': 'request_missing_logs',
                            'conversation_stage': 'evidence_collection',
                        },
                        'vmware_artifacts': [{
                            'id': 'seed-a1',
                            'session_id': 'seed-s1',
                            'filename': 'vmkernel.log',
                            'content_type': 'text/plain',
                            'size_bytes': 10,
                            'status': 'parsed_supported',
                            'artifact_type': 'vmkernel.log',
                            'uploaded_at': '2026-03-10T00:00:00',
                            'storage_path': '/tmp/seed-a1',
                            'evidence': {
                                'parser_kind': 'vmware_log_v1',
                                'artifact_type': 'vmkernel.log',
                                'parsed_line_count': 1,
                                'matched_categories': ['storage'],
                                'notable_lines': ['error'],
                                'parse_warnings': [],
                            },
                        }],
                    },
                },
            },
        ).json()['id']
        response = client.post(
            f'/api/v1/sessions/{session_id}/artifacts',
            content=b'2026-03-10 09:10:20 ERROR vmfs datastore issue',
            headers={'X-Artifact-Filename': 'vmkernel.log', 'Content-Type': 'text/plain'},
        )
        transcript = client.get(f'/api/v1/sessions/{session_id}/transcript').json()

    assert response.status_code == 201
    transition_events = [event for event in transcript['events'] if event['type'].startswith('vmware.trajectory.')]
    assert transition_events == []


def test_prompt_preface_includes_deterministic_vmware_artifact_summary(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured['payload'] = json.loads(request.content.decode())
        return httpx.Response(200, content=json.dumps({'message': {'content': 'ok'}, 'done': True}).encode() + b'\n')

    app = make_app(tmp_path, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        create = client.post('/api/v1/sessions', json={
            'title': 'VMware artifact context',
            'metadata': {
                'expert_desk': {
                    'request_label': 'req',
                    'issue_category': 'production-outage',
                    'environment_platform': 'vmware',
                    'urgency': 'same-day',
                    'preferred_expert_type': 'ai-vmware-engineer',
                    'recommended_expert_type': 'ai-vmware-engineer',
                    'recommended_path': 'launch-live-expert-now',
                    'expert_persona_id': 'ai-vmware-engineer',
                    'expert_persona_label': 'AI VMware Engineer',
                    'expert_persona_summary': '',
                    'issue_description': 'issue',
                    'architecture_notes': '',
                    'error_text': '',
                    'uploaded_logs_count': 1,
                    'uploaded_log_names': ['vmkernel.log'],
                    'uploaded_logs_available': True,
                    'vmware_artifacts': [{
                        'id': 'a1', 'session_id': 's1', 'filename': 'vmkernel.log', 'content_type': 'text/plain',
                        'size_bytes': 10, 'status': 'parsed_supported', 'artifact_type': 'vmkernel.log',
                        'uploaded_at': '2026-03-10T00:00:00', 'storage_path': '/tmp/a1',
                        'evidence': {'parser_kind': 'vmware_log_v1', 'artifact_type': 'vmkernel.log', 'parsed_line_count': 1,
                                     'timestamp_start': None, 'timestamp_end': None, 'matched_categories': ['storage'],
                                     'notable_lines': ['error'], 'parse_warnings': []},
                    }],
                },
            },
        })
        session_id = create.json()['id']
        turn = client.post(f'/api/v1/sessions/{session_id}/turns', json={'text': 'help'})

    assert turn.status_code == 201
    payload = captured['payload']
    content = '\n'.join(message['content'] for message in payload['messages'])
    assert 'VMware artifact summary (deterministic)' in content
    assert 'parsed_supported filenames: vmkernel.log' in content
    assert 'uploaded_unsupported filenames: none' in content
    assert 'parse_failed artifacts: none' in content
    assert "{'id': 'a1'" not in content
