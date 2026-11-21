import pytest

from update_planner import i18n
from update_planner.sources import discover


@pytest.fixture(autouse=True)
def german():
    """Tests prüfen deutsche Quelltexte; einzelne Tests schalten gezielt auf Englisch um."""
    i18n.set_language('de')
    yield
    i18n.set_language('de')


@pytest.fixture(autouse=True)
def discover_state():
    """catalog.load_demo() schaltet discover dauerhaft auf Demodaten – zwischen Tests zurücksetzen."""
    yield
    discover.DEMO = False
    discover.set_advisories([])
    discover._POP = None
