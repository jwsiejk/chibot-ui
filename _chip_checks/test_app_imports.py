import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import create_app
def test_import_create_app():
    app = create_app()
    assert app is not None