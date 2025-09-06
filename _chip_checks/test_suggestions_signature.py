import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.services.suggestions import hygienic_suggestions

def test_hygienic_suggestions_signature():
    a = hygienic_suggestions()
    b = hygienic_suggestions("hello")
    assert isinstance(a, list) and isinstance(b, list) and len(a) <= 4 and len(b) <= 4