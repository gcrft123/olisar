"""The built console, served with cache headers that survive an update, and compressed.

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

Text files go out compressed to a browser that accepts it: the console's main script is 1.1 MB
as built and about 300 KB compressed, and the extension editor adds 9.8 MB more. The build
writes a Brotli and a gzip copy beside each one (``web/scripts/compress.mjs``), which are sent
as they are. A file without them (a build that skipped that step) is gzipped here once and kept
in memory. Only these static files: the API's JSON stays uncompressed, since compressing
answers that mix secrets with text a visitor can influence is what BREACH reads secrets through.
"""

from __future__ import annotations

import gzip
import os
from email.utils import formatdate

import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

# Vite's output for hashed bundles (`index-D2hekc2o.js`) — web/vite.config.ts leaves assetsDir
# at its default.
HASHED_DIR = "assets/"

# What's worth compressing (web/scripts/compress.mjs picks the same), and the size below which
# it isn't.
COMPRESSIBLE = frozenset({".js", ".mjs", ".css", ".html", ".svg", ".json", ".map", ".txt", ".ttf"})
MIN_COMPRESS_BYTES = 1024

# Content-Encoding -> the suffix the build gives its copy, best first.
_ENCODINGS = (("br", ".br"), ("gzip", ".gz"))

# Files gzipped here because the build left no copy: path -> (mtime_ns, size, gzipped bytes).
_gzipped: dict[str, tuple[int, int, bytes]] = {}


def _accepted(scope: Scope) -> set[str]:
    """The encodings the request's ``Accept-Encoding`` allows (any not refused with q=0)."""
    out: set[str] = set()
    for item in Headers(scope=scope).get("accept-encoding", "").split(","):
        name, *params = [part.strip() for part in item.split(";")]
        q = 1.0
        for param in params:
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if name and q > 0:
            out.add(name.lower())
    return out


def _gzip_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return gzip.compress(f.read(), compresslevel=6, mtime=0)


class ConsoleFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if os.path.splitext(path)[1].lower() in COMPRESSIBLE:
            if isinstance(response, FileResponse) and response.status_code == 200:
                response = await self._compressed(response, scope)
            # The same URL answers differently by Accept-Encoding, so a cache has to key on it.
            response.headers["Vary"] = "Accept-Encoding"
        if path.replace("\\", "/").startswith(HASHED_DIR):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def _compressed(self, response: FileResponse, scope: Scope) -> Response:
        """``response``'s file compressed in an encoding the browser takes, or ``response``
        itself when it takes none, the file is small, or it asked for a byte range."""
        headers = Headers(scope=scope)
        stat_result = response.stat_result
        if (
            scope["method"] != "GET"
            or "range" in headers
            or stat_result is None
            or stat_result.st_size < MIN_COMPRESS_BYTES
        ):
            return response
        accepted = _accepted(scope)
        full_path = str(response.path)
        media_type = response.media_type

        for encoding, suffix in _ENCODINGS:
            if encoding not in accepted:
                continue
            try:
                copy = os.stat(full_path + suffix)
            except OSError:
                continue
            # A copy older than its file is from another build; ignore it.
            if copy.st_mtime_ns < stat_result.st_mtime_ns:
                continue
            out = FileResponse(full_path + suffix, stat_result=copy, media_type=media_type)
            out.headers["Content-Encoding"] = encoding
            return self._or_not_modified(out, headers)

        if "gzip" not in accepted:
            return response
        key = (stat_result.st_mtime_ns, stat_result.st_size)
        cached = _gzipped.get(full_path)
        if cached is None or cached[:2] != key:
            data = await anyio.to_thread.run_sync(_gzip_file, full_path)
            cached = _gzipped[full_path] = (*key, data)
        etag = response.headers["etag"].strip('"')
        out = Response(
            cached[2],
            media_type=media_type,
            headers={
                "Content-Encoding": "gzip",
                "ETag": f'"{etag}-gzip"',
                "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
            },
        )
        return self._or_not_modified(out, headers)

    def _or_not_modified(self, response: Response, request_headers: Headers) -> Response:
        """The 304 for ``response`` when the browser already holds this encoding of it."""
        if self.is_not_modified(response.headers, request_headers):
            return NotModifiedResponse(response.headers)
        return response
