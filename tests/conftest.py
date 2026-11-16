import pytest

from update_planner import i18n


@pytest.fixture(autouse=True)
def german():
    """Tests prüfen deutsche Quelltexte; einzelne Tests schalten gezielt auf Englisch um."""
    i18n.set_language('de')
    yield
    i18n.set_language('de')
