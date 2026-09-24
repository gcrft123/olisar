"""The built console, served with cache headers that survive an update.

Plain ``StaticFiles`` sends ``Last-Modified`` and an ``ETag`` but no ``Cache-Control``, which
leaves the browser to pick a freshness lifetime on its own: 10% of the file's age when it was
fetched. An ``index.html`` built days before it was loaded counts as fresh for hours after, so
the page after an update is the one from before it. That is what the desktop window showed on
2.0.beta-2: the old console's ``index.html`` straight from Chromium's cache, pointing at the old
script, which was cached too. The console ran old code against the new backend: a version line
the new build had removed, and no channel picker. A browser opening a server's console after its
image flips gets the same, or a blank page if the old script has aged out.

So ``index.html``, and everything else whose name doesn't change with its contents, is
``no-cache``: kept, but checked with the server before every use, which costs a 304 when
nothing changed. Vite content-hashes everything under ``assets/``, so a new build is a new URL
there and those files can be kept for good.
"""

from __future__ import annotations

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Vite's output for hashed bundles (`index-D2hekc2o.js`) — web/vite.config.ts leaves assetsDir
# at its default.
HASHED_DIR = "assets/"


class ConsoleFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith(HASHED_DIR):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response
