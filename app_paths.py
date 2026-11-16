"""Application resources and writable data for source and Windows releases."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
RUNTIME = Path(os.environ['LOCALAPPDATA'])/'BankedResetCalculator' if FROZEN else ROOT/'runtime'
