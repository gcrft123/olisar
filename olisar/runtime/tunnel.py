"""Expose Olisar's dashboard over Tailscale Funnel — a free, stable public HTTPS URL
(``https://<host>.<tailnet>.ts.net``) with no domain required.

We don't run the heavyweight ``tailscaled`` daemon. Instead we manage a tiny bundled Go
sidecar (``olisar-funnel``, built from ``desktop/funnel-sidecar`` with Tailscale's
``tsnet`` library) that joins the operator's tailnet using their auth key, turns on
Funnel, and reverse-proxies public traffic to Olisar's local port. The auth key is passed
to the sidecar via the environment and never leaves the machine.

The sidecar prints one machine-readable line we parse:
  ``OLISAR_FUNNEL_URL=https://...``   on success
  ``OLISAR_FUNNEL_ERROR=<reason>``    on failure (e.g. Funnel not enabled — the reason
                                       includes Tailscale's enable URL)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("olisar.tunnel")

_START_TIMEOUT = 100  # seconds to wait for the funnel to come up


def funnel_helper_path() -> str | None:
    """Locate the bundled Funnel sidecar: explicit env (Electron sets this) → next to a
    frozen bundle → anything on PATH (dev)."""
    env = os.environ.get("OLISAR_FUNNEL")
    if env and Path(env).exists():
        return env
    name = "olisar-funnel.exe" if os.name == "nt" else "olisar-funnel"
    if getattr(sys, "frozen", False):
        cand = Path(getattr(sys, "_MEIPASS", "")) / name
        if cand.exists():
            return str(cand)
    return shutil.which("olisar-funnel")


class FunnelManager:
    """Owns at most one ``olisar-funnel`` sidecar process and the public URL it reports."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._url: str = ""

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def url(self) -> str:
        return self._url

    async def start(
        self, auth_key: str, hostname: str, target: str, state_dir: str
    ) -> tuple[bool, str]:
        """Bring the funnel up. Returns ``(True, public_url)`` or ``(False, reason)``.
        ``auth_key`` may be empty on a re-launch once the node identity is persisted in
        ``state_dir``."""
        if self.running:
            return True, self._url or "running"
        exe = funnel_helper_path()
        if not exe:
            return False, "the Tailscale helper isn't bundled with this build"
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        env = {**os.environ}
        if auth_key:
            env["TS_AUTHKEY"] = auth_key
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe, "--hostname", hostname or "olisar", "--target", target,
                "--state", state_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 — surface to the caller
            log.exception("failed to launch the funnel helper")
            return False, f"failed to launch the helper: {exc}"

        try:
            async with asyncio.timeout(_START_TIMEOUT):
                assert self._proc.stdout is not None
                while True:
                    raw = await self._proc.stdout.readline()
                    if not raw:
                        break  # the sidecar exited without a marker
                    line = raw.decode(errors="replace").strip()
                    if line.startswith("OLISAR_FUNNEL_URL="):
                        self._url = line.split("=", 1)[1]
                        asyncio.create_task(self._drain())  # keep its stdout flowing
                        log.info("Tailscale Funnel up at %s", self._url)
                        return True, self._url
                    if line.startswith("OLISAR_FUNNEL_ERROR="):
                        reason = line.split("=", 1)[1]
                        await self.stop()
                        return False, reason
        except asyncio.TimeoutError:
            await self.stop()
            return False, "timed out bringing up the tunnel"
        await self.stop()
        return False, "the tunnel helper exited unexpectedly"

    async def _drain(self) -> None:
        """Consume the sidecar's remaining stdout so its pipe never blocks."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        with contextlib.suppress(Exception):
            while True:
                if not await proc.stdout.readline():
                    break

    async def stop(self) -> None:
        proc, self._proc, self._url = self._proc, None, ""
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        log.info("Tailscale Funnel stopped")
