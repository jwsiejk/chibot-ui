
from app.services.streaming import bus

def test_placeholder_bus_exists():
    assert hasattr(bus, "broadcast")
