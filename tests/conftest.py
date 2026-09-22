"""Fixtures pro testy vyžadující Home Assistant (`config_flow.py`, `coordinator.py`,
`services.py`).

`test_so_api.py` homeassistant nepotřebuje a importuje `so_api` jako čistý
top-level modul (viz `pyproject.toml`). Tyto testy naopak importují integraci
jako běžný balíček `custom_components.skolaonline_znamky` přes skutečný HA
loader/`ConfigEntry`.

Poznámka: `homeassistant` core (a tedy `pytest-homeassistant-custom-
component`) importuje na modulové úrovni `fcntl`, který na Windows
neexistuje — tyhle testy proto na nativním Windows Pythonu vůbec NEJDOU
spustit/sesbírat (import selže dřív, než se stihne cokoliv testovat).
Spouštět je jde jen na Linuxu/macOS — lokálně přes WSL, v CI běží na
`ubuntu-latest` (viz `.github/workflows/validate.yml`), takže tam to funguje
bez omezení.
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Integrace nemá `dependencies: ["recorder"]` (na rozdíl od vzorového
    repa — nepoužívá externí statistiky), takže `recorder_mock` tady není
    potřeba."""
    yield
