"""Application resource paths, independent of any live model or Qt window."""
from pathlib import Path
import sys

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else ROOT
LOCAL_REFERENCE_RESOURCES = APP_DIR / 'reference' / 'local-only' / 'resources'
LOCAL_RECOGNITION_SAMPLES = LOCAL_REFERENCE_RESOURCES / 'recognition'
