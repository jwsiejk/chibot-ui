from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from app.config import settings

app = FastAPI(title=settings.app_name)


@app.get('/health')
def health() -> JSONResponse:
    return JSONResponse({'status': 'ok', 'service': 'askchip-api'})


@app.get('/api/v1/health')
def api_v1_health() -> JSONResponse:
    return JSONResponse({'status': 'ok', 'version': 'v1'})


@app.websocket('/ws/events')
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            'type': 'placeholder',
            'message': 'AskChip local event stream scaffold is ready for future runtime events.',
        }
    )
    await websocket.close(code=1000)
