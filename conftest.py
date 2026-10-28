"""pytest setup. On a machine with no libraries configured, tests that need a real library are skipped, not failed."""
import os
from pathlib import Path

import server

if not (Path(server.__file__).resolve().parent / "libraries.json").is_file() and not os.environ.get("ENDNOTE_LIBRARIES", "").strip():
    server._libs_cache = []
