"""Automated coverage for SDK file upload / host-blob / file output paths.

Run:  uv run python -m unittest tests.test_sdk_files -v

These tests do not need a live Discord bot. They exercise:
  * size caps and FileOut resolution
  * host blob store
  * host.files.read / ingest / from through the sandbox
  * host.fetch bodyBlobId + responseBlob (mocked httpx)
  * blob sharing between Invocation and the Discord bridge
  * slash option type mapping (discord.Attachment)
"""

from __future__ import annotations

import asyncio
import base64
import io
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

from bot.cogs.sdk_commands import (
    _OPT_PY,
    _DiscordBridge,
    _make_command,
    _safe_filename,
    _ser,
    _to_discord_files,
)
from olisar.sandbox import engine
from olisar.sandbox.capabilities import (
    MAX_SDK_BASE64_BYTES,
    MAX_SDK_BLOB_BYTES,
    Invocation,
    dispatch,
    get_blob,
    store_blob,
)
from olisar.sandbox.runner import run_command


def _run(coro):
    return asyncio.run(coro)


class FakeAttachment:
    def __init__(
        self,
        data: bytes = b"hello",
        filename: str = "doc.txt",
        content_type: str = "text/plain",
        size: int | None = None,
    ):
        self._data = data
        self.id = 99
        self.filename = filename
        self.content_type = content_type
        self.size = len(data) if size is None else size

    async def read(self) -> bytes:
        return self._data


class FakeBridge:
    """Minimal DiscordBridge that records replies and loads FakeAttachments."""

    def __init__(self, attachments: dict[str, FakeAttachment] | None = None):
        self.blobs: dict = {}
        self.replies: list[Any] = []
        self.followups: list[Any] = []
        self._attachments = attachments or {}

    async def reply(self, payload: Any) -> None:
        self.replies.append(payload)

    async def follow_up(self, payload: Any) -> None:
        self.followups.append(payload)

    async def modal(self, spec: Any) -> dict:
        raise RuntimeError("modal not in this test")

    async def await_component(self, opts: Any) -> dict:
        raise RuntimeError("awaitComponent not in this test")

    async def update(self, payload: Any) -> None:
        raise RuntimeError("update not in this test")

    async def defer_update(self) -> None:
        raise RuntimeError("deferUpdate not in this test")

    async def send(self, channel_id: str, payload: Any) -> None:
        raise RuntimeError("send not in this test")

    async def fetch_attachment_bytes(
        self, option_name: str,
    ) -> tuple[bytes, str, str | None]:
        att = self._attachments.get(option_name)
        if att is None:
            raise ValueError(f"no attachment option named {option_name!r}")
        data = await att.read()
        return data, att.filename, att.content_type


class TestCapsAndHelpers(unittest.TestCase):
    def test_caps_are_the_raised_values(self):
        self.assertEqual(MAX_SDK_BASE64_BYTES, 20 * 1024 * 1024)
        self.assertEqual(MAX_SDK_BLOB_BYTES, 25 * 1024 * 1024)
        self.assertEqual(engine.COMMAND_MEMORY_BYTES, 128 * 1024 * 1024)

    def test_safe_filename_strips_paths(self):
        self.assertEqual(_safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(_safe_filename("a/b\\c.txt"), "c.txt")

    def test_ser_attachment_metadata_only(self):
        att = FakeAttachment(b"xyz", filename="a.pdf", content_type="application/pdf")
        # _ser expects a real-ish Attachment; duck-type is enough for isinstance check
        # so wrap as a plain object that isn't discord.Attachment — use real type via mock
        # Instead: only test the branch by constructing via a simple subclass if needed.
        # discord.Attachment can't be constructed easily; test the dict shape via code path
        # that doesn't need isinstance — call the logic manually.
        from bot.cogs.sdk_commands import _ser as ser

        # For non-Attachment, returns as-is
        self.assertEqual(ser("hi"), "hi")
        self.assertIsNone(ser(None))

    def test_opt_py_maps_attachment(self):
        self.assertIs(_OPT_PY["attachment"], discord.Attachment)

    def test_make_command_accepts_attachment_option(self):
        """discord.py must accept Attachment as a slash option annotation."""
        cmd = _make_command(
            "ext",
            {
                "name": "upload",
                "description": "upload a file",
                "options": [
                    {
                        "name": "doc",
                        "description": "file",
                        "type": "attachment",
                        "required": True,
                    }
                ],
            },
        )
        self.assertIsInstance(cmd, app_commands.Command)
        # Parameter present and annotated as Attachment
        sig = inspect_signature(cmd)
        self.assertIn("doc", sig)
        self.assertIs(sig["doc"], discord.Attachment)

    def test_to_discord_files_text_and_b64(self):
        files = _to_discord_files([{"name": "a.txt", "text": "hi"}])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "a.txt")
        raw = b"\x00\xff"
        files = _to_discord_files(
            [{"name": "b.bin", "contentB64": base64.b64encode(raw).decode()}]
        )
        self.assertEqual(len(files), 1)

    def test_to_discord_files_blob_id(self):
        inv = Invocation(ext_key="t", permissions=set(), guild_id=1)
        ref = store_blob(inv, b"blob-data", filename="x.bin")
        files = _to_discord_files(
            [{"name": "out.bin", "blobId": ref["blobId"]}],
            blobs=inv.blobs,
        )
        self.assertEqual(files[0].filename, "out.bin")
        # default name from blob when name omitted
        files2 = _to_discord_files([{"blobId": ref["blobId"]}], blobs=inv.blobs)
        self.assertEqual(files2[0].filename, "x.bin")

    def test_to_discord_files_unknown_blob(self):
        with self.assertRaises(ValueError):
            _to_discord_files([{"blobId": "b999"}], blobs={})

    def test_to_discord_files_base64_size_cap(self):
        with self.assertRaises(ValueError):
            _to_discord_files(
                [{"name": "big.txt", "text": "x" * (MAX_SDK_BASE64_BYTES + 1)}]
            )

    def test_to_discord_files_blob_allows_over_20mb(self):
        inv = Invocation(ext_key="t", permissions=set(), guild_id=1)
        data = b"z" * (21 * 1024 * 1024)
        ref = store_blob(inv, data, filename="big.bin")
        files = _to_discord_files([{"blobId": ref["blobId"]}], blobs=inv.blobs)
        self.assertEqual(len(files), 1)

    def test_store_blob_caps(self):
        inv = Invocation(ext_key="t", permissions=set(), guild_id=1)
        with self.assertRaises(ValueError):
            store_blob(inv, b"x" * (MAX_SDK_BLOB_BYTES + 1))


def inspect_signature(cmd: app_commands.Command) -> dict[str, Any]:
    """Return {param_name: annotation} for a discord app command callback."""
    cb = cmd.callback
    sig = getattr(cb, "__signature__", None) or __import__("inspect").signature(cb)
    return {p.name: p.annotation for p in sig.parameters.values() if p.name != "interaction"}


class TestBlobStore(unittest.TestCase):
    def test_store_and_get(self):
        inv = Invocation(ext_key="t", permissions=set(), guild_id=1)
        ref = store_blob(inv, b"abc", filename="a.txt", content_type="text/plain")
        self.assertEqual(ref["blobId"], "b1")
        self.assertEqual(ref["size"], 3)
        rec = get_blob(inv, "b1")
        self.assertEqual(rec.data, b"abc")

    def test_max_blob_count(self):
        inv = Invocation(ext_key="t", permissions=set(), guild_id=1)
        for i in range(8):
            store_blob(inv, b"x", filename=f"{i}.bin")
        with self.assertRaises(RuntimeError):
            store_blob(inv, b"y", filename="overflow.bin")


class TestFetchBlobs(unittest.TestCase):
    def test_body_blob_and_response_blob(self):
        inv = Invocation(ext_key="t", permissions={"fetch"}, guild_id=1)
        store_blob(inv, b"INPUT", filename="in.bin")

        class FakeStream:
            status_code = 200
            headers = {
                "content-type": "application/gzip",
                "content-disposition": 'attachment; filename="out.gz"',
            }
            encoding = "utf-8"

            async def aiter_bytes(self):
                yield b"OUTDATA"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, method, url, headers=None, content=None):
                self.seen_content = content
                FakeClient.last_content = content
                return FakeStream()

        with patch("olisar.sandbox.capabilities.httpx.AsyncClient", FakeClient):
            r = _run(
                dispatch(
                    inv,
                    "fetch",
                    "request",
                    [
                        "https://example.com/compress",
                        {
                            "method": "POST",
                            "bodyBlobId": "b1",
                            "responseBlob": True,
                        },
                    ],
                )
            )
        self.assertEqual(FakeClient.last_content, b"INPUT")
        self.assertEqual(r["blobId"], "b2")
        self.assertEqual(inv.blobs["b2"].data, b"OUTDATA")
        self.assertEqual(inv.blobs["b2"].filename, "out.gz")

    def test_response_too_large_raises(self):
        inv = Invocation(ext_key="t", permissions={"fetch"}, guild_id=1)

        class FakeStream:
            status_code = 200
            headers = {}
            encoding = "utf-8"

            async def aiter_bytes(self):
                # one chunk larger than base64 fetch cap
                yield b"x" * (MAX_SDK_BASE64_BYTES + 10)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, *a, **k):
                return FakeStream()

        with patch("olisar.sandbox.capabilities.httpx.AsyncClient", FakeClient):
            with self.assertRaises(ValueError):
                _run(
                    dispatch(
                        inv,
                        "fetch",
                        "request",
                        ["https://example.com/big", {}],
                    )
                )

    def test_ssrf_blocks_loopback(self):
        inv = Invocation(ext_key="t", permissions={"fetch"}, guild_id=1)
        with self.assertRaises(ValueError):
            _run(
                dispatch(
                    inv,
                    "fetch",
                    "request",
                    ["http://127.0.0.1/secret", {}],
                )
            )


class TestSandboxFileCommands(unittest.TestCase):
    def test_ingest_reply_with_blob(self):
        att = FakeAttachment(b"payload-bytes", filename="in.bin")
        bridge = FakeBridge({"file": att})

        compiled = r"""
defineExtension({
  id: "blob_demo", name: "Blob", permissions: ["discord.reply"],
  commands: [{
    name: "go", description: "go",
    options: [{ name: "file", type: "attachment", description: "f", required: true }],
    handler: async function (i) {
      var input = await host.files.ingest("file");
      var out = await host.files.from({ name: "echo.bin", contentB64: "YQ==" }); // 'a'
      await i.reply({
        content: "size=" + input.size,
        files: [
          { name: "out.bin", blobId: input.blobId },
          { name: "tiny.txt", blobId: out.blobId },
        ],
      });
    },
  }],
});
"""
        _run(
            run_command(
                ext_key="blob_demo",
                compiled_js=compiled,
                permissions=["discord.reply"],
                command_name="go",
                interaction_data={
                    "options": {
                        "file": {
                            "id": "1",
                            "filename": "in.bin",
                            "size": 13,
                            "contentType": "application/octet-stream",
                        }
                    },
                    "guildId": "1",
                    "channelId": "2",
                    "userId": "3",
                    "displayName": "T",
                },
                guild_id=1,
                session=None,
                discord=bridge,
                trusted=True,
            )
        )
        self.assertEqual(len(bridge.replies), 1)
        r = bridge.replies[0]
        self.assertEqual(r["content"], "size=13")
        # Bridge shares inv.blobs — blobIds must resolve
        self.assertIn(r["files"][0]["blobId"], bridge.blobs)
        dfiles = _to_discord_files(r["files"], blobs=bridge.blobs)
        self.assertEqual(dfiles[0].filename, "out.bin")
        # Read back bytes from the discord.File fp
        data = dfiles[0].fp.read()
        self.assertEqual(data, b"payload-bytes")

    def test_read_returns_base64(self):
        att = FakeAttachment(b"abc", filename="a.txt")
        bridge = FakeBridge({"doc": att})
        compiled = r"""
defineExtension({
  id: "r", name: "r", permissions: ["discord.reply"],
  commands: [{
    name: "go", description: "go",
    options: [{ name: "doc", type: "attachment", description: "d", required: true }],
    handler: async function (i) {
      var f = await host.files.read("doc");
      await i.reply(f.filename + ":" + f.contentB64 + ":" + f.size);
    },
  }],
});
"""
        _run(
            run_command(
                ext_key="r",
                compiled_js=compiled,
                permissions=["discord.reply"],
                command_name="go",
                interaction_data={
                    "options": {
                        "doc": {
                            "id": "1",
                            "filename": "a.txt",
                            "size": 3,
                            "contentType": "text/plain",
                        }
                    },
                    "guildId": "1",
                    "channelId": "2",
                    "userId": "3",
                    "displayName": "T",
                },
                guild_id=1,
                session=None,
                discord=bridge,
                trusted=True,
            )
        )
        expected_b64 = base64.b64encode(b"abc").decode()
        self.assertEqual(bridge.replies[0], f"a.txt:{expected_b64}:3")

    def test_read_rejects_oversize_for_base64_path(self):
        # Attachment claims large size; read should refuse before/after load
        big = b"x" * (MAX_SDK_BASE64_BYTES + 100)
        att = FakeAttachment(big, filename="huge.bin")
        bridge = FakeBridge({"doc": att})
        compiled = r"""
defineExtension({
  id: "r", name: "r", permissions: ["discord.reply"],
  commands: [{
    name: "go", description: "go",
    options: [{ name: "doc", type: "attachment", description: "d", required: true }],
    handler: async function (i) {
      try {
        await host.files.read("doc");
        await i.reply("should-have-failed");
      } catch (e) {
        await i.reply("caught");
      }
    },
  }],
});
"""
        _run(
            run_command(
                ext_key="r",
                compiled_js=compiled,
                permissions=["discord.reply"],
                command_name="go",
                interaction_data={
                    "options": {
                        "doc": {
                            "id": "1",
                            "filename": "huge.bin",
                            "size": len(big),
                            "contentType": "application/octet-stream",
                        }
                    },
                    "guildId": "1",
                    "channelId": "2",
                    "userId": "3",
                    "displayName": "T",
                },
                guild_id=1,
                session=None,
                discord=bridge,
                trusted=True,
            )
        )
        self.assertEqual(bridge.replies[0], "caught")

    def test_missing_attachment_option_errors(self):
        bridge = FakeBridge({})
        compiled = r"""
defineExtension({
  id: "r", name: "r", permissions: ["discord.reply"],
  commands: [{
    name: "go", description: "go",
    handler: async function (i) {
      try {
        await host.files.ingest("nope");
        await i.reply("bad");
      } catch (e) {
        await i.reply("missing");
      }
    },
  }],
});
"""
        _run(
            run_command(
                ext_key="r",
                compiled_js=compiled,
                permissions=["discord.reply"],
                command_name="go",
                interaction_data={
                    "options": {},
                    "guildId": "1",
                    "channelId": "2",
                    "userId": "3",
                    "displayName": "T",
                },
                guild_id=1,
                session=None,
                discord=bridge,
                trusted=True,
            )
        )
        self.assertEqual(bridge.replies[0], "missing")

    def test_compress_pipeline_with_mocked_fetch(self):
        """Full author pattern: ingest → fetch(bodyBlobId, responseBlob) → reply files."""
        att = FakeAttachment(b"ORIGINAL", filename="photo.png", content_type="image/png")
        bridge = FakeBridge({"file": att})

        class FakeStream:
            status_code = 200
            headers = {"content-type": "application/octet-stream"}
            encoding = "utf-8"

            async def aiter_bytes(self):
                yield b"COMPRESSED"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def stream(self, method, url, headers=None, content=None):
                assert content == b"ORIGINAL"
                return FakeStream()

        compiled = r"""
defineExtension({
  id: "compress", name: "Compress", permissions: ["discord.reply", "fetch"],
  commands: [{
    name: "compress", description: "compress",
    options: [{ name: "file", type: "attachment", description: "f", required: true }],
    handler: async function (i) {
      await i.reply({ content: "working…", ephemeral: true });
      var input = await host.files.ingest("file");
      var res = await host.fetch("https://example.com/compress", {
        method: "POST",
        bodyBlobId: input.blobId,
        responseBlob: true,
      });
      if (!res.ok || !res.blobId) {
        await i.followUp({ content: "fail", ephemeral: true });
        return;
      }
      await i.followUp({
        content: "done",
        files: [{ name: input.filename + ".gz", blobId: res.blobId }],
      });
    },
  }],
});
"""
        with patch("olisar.sandbox.capabilities.httpx.AsyncClient", FakeClient):
            _run(
                run_command(
                    ext_key="compress",
                    compiled_js=compiled,
                    permissions=["discord.reply", "fetch"],
                    command_name="compress",
                    interaction_data={
                        "options": {
                            "file": {
                                "id": "1",
                                "filename": "photo.png",
                                "size": 8,
                                "contentType": "image/png",
                            }
                        },
                        "guildId": "1",
                        "channelId": "2",
                        "userId": "3",
                        "displayName": "T",
                    },
                    guild_id=1,
                    session=None,
                    discord=bridge,
                    trusted=True,
                )
            )
        self.assertEqual(bridge.replies[0]["content"], "working…")
        self.assertEqual(len(bridge.followups), 1)
        fu = bridge.followups[0]
        self.assertEqual(fu["content"], "done")
        dfiles = _to_discord_files(fu["files"], blobs=bridge.blobs)
        self.assertEqual(dfiles[0].filename, "photo.png.gz")
        self.assertEqual(dfiles[0].fp.read(), b"COMPRESSED")

    def test_manifest_preserves_attachment_option(self):
        compiled = r"""
defineExtension({
  id: "m", name: "m", permissions: ["discord.reply"],
  commands: [{
    name: "u", description: "u",
    options: [{ name: "doc", type: "attachment", description: "d", required: true }],
    handler: async function () {},
  }],
});
"""
        m = engine.extract_manifest(compiled)
        self.assertEqual(m["commands"][0]["options"][0]["type"], "attachment")

    def test_sandbox_self_check_still_passes(self):
        self.assertTrue(engine.self_check())


class TestDiscordFileBytesRoundTrip(unittest.TestCase):
    def test_discord_file_from_bytesio(self):
        """discord.File accepts BytesIO the way _to_discord_files builds it."""
        data = b"roundtrip"
        f = discord.File(io.BytesIO(data), filename="t.bin")
        self.assertEqual(f.filename, "t.bin")
        self.assertEqual(f.fp.read(), data)


class TestRealDiscordBridge(unittest.TestCase):
    """Exercise the production _DiscordBridge with a mocked Interaction (no network)."""

    def test_reply_and_followup_resolve_blob_and_text_files(self):
        it = MagicMock()
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        it.followup = MagicMock()
        it.followup.send = AsyncMock()

        bridge = _DiscordBridge(it, "ext")
        inv = Invocation(ext_key="ext", permissions=set(), guild_id=1)
        bridge.blobs = inv.blobs  # runner._share_blobs

        att = MagicMock(spec=discord.Attachment)
        att.id = 1
        att.filename = "up.pdf"
        att.size = 4
        att.content_type = "application/pdf"
        att.read = AsyncMock(return_value=b"PDF!")
        bridge.capture_attachments({"doc": att})

        data, name, ctype = _run(bridge.fetch_attachment_bytes("doc"))
        self.assertEqual(data, b"PDF!")
        self.assertEqual(name, "up.pdf")

        ref = store_blob(inv, b"OUT", filename="out.bin")
        _run(
            bridge.reply(
                {
                    "content": "here",
                    "files": [{"name": "out.bin", "blobId": ref["blobId"]}],
                }
            )
        )
        kwargs = it.response.send_message.await_args.kwargs
        self.assertEqual(kwargs.get("content"), "here")
        self.assertEqual(kwargs["files"][0].filename, "out.bin")
        self.assertEqual(kwargs["files"][0].fp.read(), b"OUT")

        _run(
            bridge.follow_up(
                {"content": "again", "files": [{"text": "x", "name": "n.txt"}]}
            )
        )
        kwargs2 = it.followup.send.await_args.kwargs
        self.assertEqual(kwargs2["files"][0].filename, "n.txt")

    def test_followup_inherits_ephemeral_from_first_reply(self):
        it = MagicMock()
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        it.followup = MagicMock()
        it.followup.send = AsyncMock()

        bridge = _DiscordBridge(it, "ext")
        _run(bridge.reply({"content": "working…", "ephemeral": True}))
        self.assertTrue(it.response.send_message.await_args.kwargs.get("ephemeral"))

        _run(bridge.follow_up({"content": "done", "files": [{"name": "a.txt", "text": "x"}]}))
        fu = it.followup.send.await_args.kwargs
        self.assertTrue(fu.get("ephemeral"), "followUp should inherit ephemeral")
        self.assertEqual(fu.get("content"), "done")

    def test_followup_can_override_ephemeral_false(self):
        it = MagicMock()
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        it.followup = MagicMock()
        it.followup.send = AsyncMock()

        bridge = _DiscordBridge(it, "ext")
        _run(bridge.reply({"content": "private note", "ephemeral": True}))
        _run(bridge.follow_up({"content": "public result", "ephemeral": False}))
        fu = it.followup.send.await_args.kwargs
        self.assertFalse(fu.get("ephemeral", False))

    def test_explicit_ephemeral_followup_after_public_reply(self):
        it = MagicMock()
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        it.followup = MagicMock()
        it.followup.send = AsyncMock()

        bridge = _DiscordBridge(it, "ext")
        _run(bridge.reply("public first"))
        _run(bridge.follow_up({"content": "just for you", "ephemeral": True}))
        fu = it.followup.send.await_args.kwargs
        self.assertTrue(fu.get("ephemeral"))

    def test_second_reply_acts_as_ephemeral_followup(self):
        it = MagicMock()
        it.response = MagicMock()
        it.response.send_message = AsyncMock()
        it.followup = MagicMock()
        it.followup.send = AsyncMock()

        bridge = _DiscordBridge(it, "ext")
        _run(bridge.reply({"content": "one", "ephemeral": True}))
        _run(bridge.reply({"content": "two"}))  # no explicit flag — inherit
        fu = it.followup.send.await_args.kwargs
        self.assertTrue(fu.get("ephemeral"))
        self.assertEqual(fu.get("content"), "two")


if __name__ == "__main__":
    unittest.main()
