from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from app.api_models import (
    ConfigResponse,
    CreateSessionRequest,
    CreateTurnRequest,
    HealthResponse,
    RenameSessionRequest,
    TranscriptMessageResponse,
    TranscriptResponse,
)
from app.config import Settings, settings
from app.domain_models import EventRecord, SessionRecord
from app.events import EventBus
from app.ollama import OllamaClient, OllamaUnavailableError
from app.prompting import PromptAssembler
from app.storage import Database, DatabaseError
from app.turns import BusyError, TurnManager
from app.webrtc import WebRtcSignalingService, WebRtcWebSocketHandler
from app.webrtc_models import WebRtcOfferRequest


class AppState:
    def __init__(self, config: Settings, ollama_transport=None, webrtc_peer_factory=None) -> None:
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
        self.webrtc_signaling = WebRtcSignalingService(peer_factory=webrtc_peer_factory)
        self.webrtc_websocket = WebRtcWebSocketHandler(self.webrtc_signaling)


def create_app(config: Settings = settings, ollama_transport=None, webrtc_peer_factory=None) -> FastAPI:
    state = AppState(config, ollama_transport=ollama_transport, webrtc_peer_factory=webrtc_peer_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.db.initialize()
        now = datetime.now(timezone.utc).isoformat()
        state.db.upsert_setting('ollama_base_url', config.ollama_base_url, now)
        state.db.upsert_setting('ollama_model', config.ollama_model, now)
        app.state.askchip = state
        yield
        await state.webrtc_signaling.clear()

    app = FastAPI(title=config.app_name, lifespan=lifespan)

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
        )
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

    @app.post('/api/v1/sessions/{session_id}/turns')
    async def create_turn(session_id: str, request: CreateTurnRequest) -> JSONResponse:
        session = state.db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail='session not found')
        if not request.text.strip():
            raise HTTPException(status_code=422, detail='text is required')
        try:
            payload = await state.turn_manager.handle_typed_turn(session, request.text.strip())
            return JSONResponse({'status': 'completed', **payload}, status_code=201)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OllamaUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.websocket('/ws/events')
    async def websocket_events(websocket: WebSocket, session_id: str | None = Query(default=None)) -> None:
        await state.event_bus.connect(websocket, session_id)
        lifecycle = EventRecord(session_id=session_id, type='connection.lifecycle', payload={'state': 'connected'})
        state.db.create_event(lifecycle)
        await state.event_bus.publish(state.turn_manager._event_payload(lifecycle), session_id)
        state_event = EventRecord(session_id=session_id, type='state', payload={'state': 'ready', 'detail': 'event_stream_connected'})
        state.db.create_event(state_event)
        await state.event_bus.publish(state.turn_manager._event_payload(state_event), session_id)
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
