"""Project paths and shared constants."""

from pathlib import Path

# constants.py -> core -> houtools -> python3.13libs -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

PYTHON_LIBS_DIR = PROJECT_ROOT / "python3.13libs"
MENU_FILE = PROJECT_ROOT / "MainMenuCommon.xml"
SETTINGS_DIR = PROJECT_ROOT / "settings"
