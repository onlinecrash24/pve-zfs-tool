"""Pytest bootstrap: make the repo root importable so `import app.x` works,
and point DATA_DIR-using modules at a throwaway temp dir for the rare test
that touches the filesystem."""

import os
import sys
import tempfile

# Repo root = parent of the tests/ directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Some app modules hardcode DATA_DIR="/app/data". None of the pure functions
# under test write there, but set a sane HOME/TMP just in case a module does
# an os.makedirs at import on some platform.
os.environ.setdefault("TMPDIR", tempfile.gettempdir())
