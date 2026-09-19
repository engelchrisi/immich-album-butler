"""Design mode: the web UI for building album rules.

Split in two so the interesting half can be tested without a browser:

* `api.py` is the whole feature as plain Python -- pickers, preview, analyze,
  save, run -- taking and returning dictionaries.
* `server.py` is a thin HTTP layer over it: routing, auth, and the static files.
"""

from .server import serve

__all__ = ["serve"]
