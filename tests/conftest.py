import sys
from pathlib import Path

# Make `src/` importable in the test process.
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
