from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from app.api_models import (
    ConfigResponse,
    CreateSessionRequest,
    CreateTurnRequest,
    HealthResponse,
    ReadinessResponse,
    RenameSessionRequest,
    TranscriptMessageResponse,
    TranscriptResponse,
)
from app.config import Settings, settings
from app.domain_models import EventRecord, SessionRecord
from app.events import EventBus
from app.ollama import OllamaClient, OllamaUnavailableError
from app.prompting import PromptAssembler
from app.readiness import ReadinessTracker
from app.speech import SpeechService
from app.storage import Database, DatabaseError
from app.stt import FasterWhisperSttService
from app.tts import KokoroConfig, KokoroTtsAdapter, TtsError, configure_kokoro_runtime
from app.turns import BusyError, InvalidCommittedInputError, TurnManager
from app.voice import EmptyTranscriptionError, InvalidVoiceLifecycleError, VoiceInputError, VoiceTurnService
from app.webrtc import WebRtcSignalingService, WebRtcWebSocketHandler
from app.webrtc_models import WebRtcOfferRequest


logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, config: Settings, ollama_transport=None, webrtc_peer_factory=None, stt_service=None, tts_adapter=None) -> None:
        self.config = config
        self.db = Database(Path(config.database_path))
        self.event_bus = EventBus()
        self.prompt_assembler = PromptAssembler(transcript_window=config.prompt_transcript_window)
        self.ollama = OllamaClient(
            base_url=config.ollama_base_url,
            model=config.ollama_model,
            timeout_seconds=config.ollama_timeout_seconds,
            transport=ollama_transport,
        )
        self.turn_manager = TurnManager(self.db, self.event_bus, self.ollama, self.prompt_assembler)
        configure_kokoro_runtime(KokoroConfig(
            voice=config.tts_voice,
            model_path=str(config.tts_model_path) if config.tts_model_path else None,
            voices_path=str(config.tts_voices_path) if config.tts_voices_path else None,
            device=config.tts_device,
            sample_rate_hz=config.tts_sample_rate_hz,
            speed=config.tts_speed,
            lang_code=config.tts_lang_code,
        ))
        self.stt = stt_service or FasterWhisperSttService(
            model_name=config.stt_model,
            device=config.stt_device,
            compute_type=config.stt_compute_type,
            cpu_threads=config.stt_cpu_threads,
        )
        self.voice_turns = VoiceTurnService(self.db, self.event_bus, self.stt, self.turn_manager)
        self.speech = SpeechService(self.db, self.event_bus, self.turn_manager, tts_adapter or KokoroTtsAdapter(KokoroConfig(
            voice=config.tts_voice,
            model_path=str(config.tts_model_path) if config.tts_model_path else None,
            voices_path=str(config.tts_voices_path) if config.tts_voices_path else None,
            device=config.tts_device,
            sample_rate_hz=config.tts_sample_rate_hz,
            speed=config.tts_speed,
            lang_code=config.tts_lang_code,
        )))
        self.webrtc_signaling = WebRtcSignalingService(peer_factory=webrtc_peer_factory)
        self.webrtc_websocket = WebRtcWebSocketHandler(self.webrtc_signaling)
        self.readiness = ReadinessTracker(
            ollama=self.ollama,
            tts=self.speech.tts,
            ollama_warmup_enabled=config.ollama_warmup_enabled,
            tts_warmup_enabled=config.tts_warmup_enabled,
        )

        stt_runtime = self.stt.runtime_details() if hasattr(self.stt, 'runtime_details') else {'stt_model': config.stt_model, 'selected_device': config.stt_device, 'compute_type': config.stt_compute_type}
        tts_runtime = self.speech.tts.runtime_details() if hasattr(self.speech.tts, 'runtime_details') else {'tts_engine': 'kokoro-onnx', 'selected_device': config.tts_device}
        logger.info('askchip_runtime_selection stt=%s tts=%s', stt_runtime, tts_runtime)


def create_app(config: Settings = settings, ollama_transport=None, webrtc_peer_factory=None, stt_service=None, tts_adapter=None) -> FastAPI:
    state = AppState(config, ollama_transport=ollama_transport, webrtc_peer_factory=webrtc_peer_factory, stt_service=stt_service, tts_adapter=tts_adapter)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.db.initialize()
        now = datetime.now(timezone.utc).isoformat()
        state.db.upsert_setting('ollama_base_url', config.ollama_base_url, now)
        state.db.upsert_setting('ollama_model', config.ollama_model, now)
        state.db.upsert_setting('stt_model', config.stt_model, now)
        state.db.upsert_setting('tts_voice', config.tts_voice, now)
        app.state.askchip = state
        state.readiness.start()
        yield
        await state.webrtc_signaling.clear()

    app = FastAPI(title=config.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'],
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.get('/health')
    def health() -> JSONResponse:
        return JSONResponse(HealthResponse(status='ok', service='askchip-api').model_dump())

    @app.get('/api/v1/health')
    def api_v1_health() -> JSONResponse:
        return JSONResponse({'status': 'ok', 'version': 'v1'})

    @app.get('/api/v1/config')
    def get_config() -> JSONResponse:
        payload = ConfigResponse(
            app_name=config.app_name,
            ollama_base_url=config.ollama_base_url,
            ollama_model=config.ollama_model,
            database_path=str(config.database_path),
            stt_model=config.stt_model,
            stt_device=config.stt_device,
            stt_compute_type=str((state.stt.runtime_details().get('compute_type') if hasattr(state.stt, 'runtime_details') else config.stt_compute_type)),
            tts_voice=config.tts_voice,
            tts_device=str((state.speech.tts.runtime_details().get('selected_device') if hasattr(state.speech.tts, 'runtime_details') else config.tts_device)),
            tts_model_path=str(config.tts_model_path) if config.tts_model_path else None,
            tts_voices_path=str(config.tts_voices_path) if config.tts_voices_path else None,
            tts_sample_rate_hz=config.tts_sample_rate_hz,
            tts_speed=config.tts_speed,
            tts_lang_code=config.tts_lang_code,
            ollama_warmup_enabled=config.ollama_warmup_enabled,
            tts_warmup_enabled=config.tts_warmup_enabled,
        )
        return JSONResponse(payload.model_dump())

    @app.get('/api/v1/readiness')
    def get_readiness() -> JSONResponse:
        payload = ReadinessResponse(**state.readiness.snapshot())
        return JSONResponse(payload.model_dump())

    @app.post('/api/v1/webrtc/offer')
    async def create_webrtc_offer(request: WebRtcOfferRequest) -> JSONResponse:
        response = await state.webrtc_signaling.negotiate_offer(session_id=request.session_id, offer=request.offer)
        return JSONResponse(response.model_dump(), status_code=200, headers={'X-AskChip-WebRTC-Compatibility': 'http-offer'})

    @app.websocket('/ws/webrtc')
    async def websocket_webrtc_signaling(websocket: WebSocket) -> None:
        await state.webrtc_websocket.handle(websocket)

    @app.post('/api/v1/sessions')
    def create_session(request: CreateSessionRequest) -> JSONResponse:
        session = SessionRecord(title=request.title or 'New chat', status='ready', ready_at=datetime.now(timezone.utc))
        state.db.create_session(session)
        return JSONResponse(session.model_dump(mode='json'), status_code=201)

    @app.get('/api/v1/sessions')
    def list_sessions() -> JSONResponse:
        sessions = [session.model_dump(mode='json') for session in state.db.list_sessions()]
        return JSONResponse({'items': sessions})

    @app.patch('/api/v1/sessions/{session_id}')
    def rename_session(session_id: str, request: RenameSessionRequest) -> JSONResponse:
        updated = state.db.rename_session(session_id, request.title, datetime.now(timezone.utc).isoformat())
        if updated is None:
            raise HTTPException(status_code=404, detail='session not found')
        return JSONResponse(updated.model_dump(mode='json'))

    @app.delete('/api/v1/sessions/{session_id}')
    def delete_session(session_id: str) -> JSONResponse:
        if not state.db.delete_session(session_id):
            raise HTTPException(status_code=404, detail='session not found')
        return JSONResponse({'status': 'deleted', 'session_id': session_id})

    @app.get('/api/v1/sessions/{session_id}/transcript')
    def get_transcript(session_id: str) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        transcript = TranscriptResponse(
            session=session,
            messages=[TranscriptMessageResponse.from_record(message) for message in state.db.list_messages(session_id)],
            events=state.db.list_events(session_id),
            timings=state.db.list_timings(session_id),
        )
        return JSONResponse(transcript.model_dump(mode='json'))


    @app.get('/api/v1/sessions/{session_id}/messages/{message_id}/speech')
    def get_assistant_speech(session_id: str, message_id: str, text: str | None = Query(default=None)) -> Response:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        try:
            speech = state.speech.synthesize_message(session_id, message_id, text=text)
            return Response(content=speech.audio_bytes, media_type=speech.content_type)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TtsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/messages/{message_id}/speech/start')
    async def start_assistant_speech(session_id: str, message_id: str) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        try:
            await state.speech.start_playback(session_id, message_id)
            return JSONResponse({'status': 'speaking'})
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/messages/{message_id}/speech/stop')
    async def stop_assistant_speech(session_id: str, message_id: str, request: Request) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        payload = await request.json() if request.headers.get('content-type', '').startswith('application/json') else {}
        reason = payload.get('reason') if isinstance(payload, dict) else None
        try:
            status = await state.speech.stop_playback(session_id, message_id, reason=str(reason or 'stopped'))
            return JSONResponse({'status': status})
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/turns')
    async def create_turn(session_id: str, request: CreateTurnRequest, trace_id: str | None = Header(default=None, alias='X-AskChip-Trace-Id')) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        if not request.text.strip():
            raise HTTPException(status_code=422, detail='text is required')
        try:
            payload = await state.turn_manager.handle_typed_turn(session, request.text.strip(), trace_id=trace_id)
            return JSONResponse({'status': 'completed', **payload}, status_code=201)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InvalidCommittedInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OllamaUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/voice-turns/ptt/start')
    async def start_voice_turn(session_id: str, device_id: str | None = Header(default=None, alias='X-AskChip-Device-Id'), trace_id: str | None = Header(default=None, alias='X-AskChip-Trace-Id')) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        try:
            await state.voice_turns.begin_ptt(session, device_id=device_id, trace_id=trace_id)
            return JSONResponse({'status': 'listening'})
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/voice-turns/ptt/cancel')
    async def cancel_voice_turn(session_id: str) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        try:
            await state.voice_turns.cancel_ptt(session.id)
            return JSONResponse({'status': 'ready'})
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post('/api/v1/sessions/{session_id}/voice-turns')
    async def create_voice_turn(
        session_id: str,
        request: Request,
        filename: str | None = Query(default='voice-turn.webm'),
        device_id: str | None = Header(default=None, alias='X-AskChip-Device-Id'),
        duration_ms: int | None = Header(default=None, alias='X-AskChip-Duration-Ms'),
        trace_id: str | None = Header(default=None, alias='X-AskChip-Trace-Id'),
    ) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        audio_bytes = await request.body()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail='audio is required')
        try:
            payload = await state.voice_turns.handle_ptt_release(
                session,
                audio_bytes=audio_bytes,
                filename=filename or 'voice-turn.webm',
                mime_type=request.headers.get('content-type'),
                device_id=device_id,
                duration_ms=duration_ms,
                trace_id=trace_id,
            )
            return JSONResponse({'status': 'completed', **payload}, status_code=201)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except EmptyTranscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except InvalidCommittedInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except InvalidVoiceLifecycleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except VoiceInputError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.websocket('/ws/events')
    async def websocket_events(websocket: WebSocket, session_id: str | None = Query(default=None)) -> None:
        await state.event_bus.connect(websocket, session_id)
        lifecycle = EventRecord(session_id=session_id, type='connection.lifecycle', payload={'state': 'connected'})
        state.db.create_event(lifecycle)
        await state.event_bus.publish(state.turn_manager.event_payload(lifecycle), session_id)
        state_event = EventRecord(session_id=session_id, type='state', payload={'state': 'ready', 'detail': 'event_stream_connected'})
        state.db.create_event(state_event)
        await state.event_bus.publish(state.turn_manager.event_payload(state_event), session_id)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            disconnect_event = EventRecord(session_id=session_id, type='connection.lifecycle', payload={'state': 'disconnected'})
            state.db.create_event(disconnect_event)
            await state.event_bus.disconnect(websocket, session_id)
        except RuntimeError:
            await state.event_bus.disconnect(websocket, session_id)

    return app


app = create_app()
