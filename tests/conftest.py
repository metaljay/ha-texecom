"""Driver tests (tests/*.py) run without Home Assistant; tests/ha needs it."""

try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:
    collect_ignore = ["ha"]
