"""Legacy SSE bridge removed.

This module previously exposed a Flask route that mirrored WebSocket
traffic over Server-Sent Events. The project now relies solely on the
native WebSocket implementation, so the helper remains only as a stub
for older imports.
"""


def register_ws_route(app):
    """Retained for backwards compatibility; no SSE route is registered."""
    return None
