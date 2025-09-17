from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class OriginCheckMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins=None):
        super().__init__(app)
        self.allowed = set(allowed_origins or [])

    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin")
        if self.allowed and origin and not any(origin.startswith(a) for a in self.allowed):
            return JSONResponse({"ok": False, "error": "origin not allowed"}, status_code=403)
        return await call_next(request)
