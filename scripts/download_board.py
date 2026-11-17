#!/usr/bin/env python3
"""Download images from a public Pinterest board (stdlib only, no login).

Thin wrapper: the implementation lives in backend/api/vibes/pinterest.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from api.vibes.pinterest import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
