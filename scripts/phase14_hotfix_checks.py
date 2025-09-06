#!/usr/bin/env python3
import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# Should import without circular import errors
import app.asgi_gateway as gw
import app.ws.ws_asgi as wsa
import app.drain_state as ds
print("HOTFIX: imports OK; draining initial:", ds.is_draining())
