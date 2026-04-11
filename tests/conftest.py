# tests/conftest.py
import pytest

# pytest-asyncio auto mode settings
def pytest_configure(config):
    """Set asyncio_mode to auto so all async tests/fixtures work without manual decoration."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
