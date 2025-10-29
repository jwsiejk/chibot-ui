import importlib, inspect, sys
try:
    mod = importlib.import_module("app.ws.adapter")
    print("IMPORT PATH:", mod.__file__)
    A = getattr(mod, "ChatV2Adapter", None)
    print("HAS ChatV2Adapter:", A is not None)
    print("HAS _publish:", hasattr(A, "_publish"))
    if A:
        print("_publish signature:", inspect.signature(A._publish))
except Exception as e:
    print("IMPORT FAILED:", repr(e))
    sys.exit(1)