"""Moving a bot off a VM brings back plain files under plain names, and nothing else.

Run:  uv run python -m unittest tests.test_vm_download -v

A move from a VM downloads its database and its uploaded documents over SFTP. The documents
came down with one recursive get, which writes each entry under whatever name the server
lists (asyncssh joins "../x" on as given) and copies a symlink as a symlink pointing wherever
the server says; even a single-file get copies a symlink that way. A compromised VM could
then plant files outside the download, or links the move would read through (and upload to
the next VM) or write through (over a file of its choosing). Now the database files are
fetched by their fixed names as plain files, and the documents one at a time, each checked
with lstat and refused unless it's a plain file with a plain name. The local copy steps skip
symlinks too, and replace (rather than write through) one already at a destination name.

The SFTP side is a fake that behaves the way asyncssh's client does for those cases.
"""

from __future__ import annotations

import os
import posixpath
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import asyncssh

from olisar.runtime import migrate, remote

HOME = "/home/ubuntu"
EXPORT = f"{HOME}/olisar/export"


class _Node:
    def __init__(self, kind: str, data: bytes = b"", target: str = "",
                 children: dict | None = None) -> None:
        self.kind, self.data, self.target, self.children = kind, data, target, children or {}

    @property
    def type(self) -> int:
        return {
            "file": asyncssh.FILEXFER_TYPE_REGULAR,
            "dir": asyncssh.FILEXFER_TYPE_DIRECTORY,
            "link": asyncssh.FILEXFER_TYPE_SYMLINK,
        }[self.kind]


class _FakeSFTP:
    """A VM's export directory, served the way asyncssh's client handles it."""

    def __init__(self, tree: dict[str, _Node]) -> None:
        self.tree = tree

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def realpath(self, path):
        return HOME

    def _node(self, path: str) -> _Node:
        parts = posixpath.relpath(path, EXPORT).split("/")
        node = _Node("dir", children=self.tree)
        for part in parts:
            if part == ".":
                continue
            if node.kind != "dir" or part not in node.children:
                raise asyncssh.SFTPNoSuchFile(path)
            node = node.children[part]
        return node

    def _followed(self, node: _Node) -> _Node:
        # The server resolves a link on the VM itself; its bytes are the VM's to choose.
        return _Node("file", data=b"what the link points at, on the VM") if node.kind == "link" else node

    async def stat(self, path, *, follow_symlinks=True):
        node = self._node(path)
        return self._followed(node) if follow_symlinks else node

    async def lstat(self, path):
        return self._node(path)

    async def readdir(self, path):
        names = [".", "..", *self._node(path).children]
        return [SimpleNamespace(filename=n) for n in names]

    async def get(self, src, dst, *, recurse=False, follow_symlinks=False, **_):
        await self._copy(src, str(dst), self._node(src), recurse, follow_symlinks)

    async def _copy(self, src: str, dst: str, node: _Node, recurse: bool, follow: bool) -> None:
        if follow:
            node = self._followed(node)
        if node.kind == "link":
            os.symlink(node.target, dst)
        elif node.kind == "dir":
            if not recurse:
                raise asyncssh.SFTPFailure(f"{src} is a directory")
            os.makedirs(dst, exist_ok=True)
            for name, child in node.children.items():
                await self._copy(posixpath.join(src, name), posixpath.join(dst, name),
                                 child, recurse, follow)
        else:
            Path(dst).write_bytes(node.data)


class _FakeConn:
    def __init__(self, sftp: _FakeSFTP) -> None:
        self.sftp = sftp

    async def run(self, cmd, input=None, check=False):  # noqa: A002 — asyncssh's name
        out = "olisar_olisar-data\n" if "docker volume ls" in cmd else ""
        return SimpleNamespace(stdout=out, stderr="", exit_status=0)

    def start_sftp_client(self):
        return self.sftp

    def close(self) -> None:
        pass


class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.secret = self.root / "id_ed25519"
        self.secret.write_text("PRIVATE KEY")
        self.dest = self.root / "move" / "staging"

    async def export(self, tree: dict[str, _Node]) -> None:
        conn = _FakeConn(_FakeSFTP(tree))
        with mock.patch.object(remote, "_connect", mock.AsyncMock(return_value=conn)):
            await remote.export_data("203.0.113.5", "ubuntu", self.dest, "olisar")

    def symlinks(self) -> list[str]:
        return [
            str(Path(d) / n)
            for d, dirs, files in os.walk(self.root)
            for n in dirs + files
            if (Path(d) / n).is_symlink()
        ]

    async def test_documents_come_back_as_plain_files_only(self) -> None:
        await self.export({
            "olisar.db": _Node("file", b"DB"),
            "kb_uploads": _Node("dir", children={
                "manual.pdf": _Node("file", b"PDF"),
                "key.pdf": _Node("link", target=str(self.secret)),
                "../../escaped.txt": _Node("file", b"planted"),
                str(self.root / "absolute.txt"): _Node("file", b"planted"),
                "nested": _Node("dir", children={"inner.txt": _Node("file", b"x")}),
            }),
        })
        kb = self.dest / "kb_uploads"
        self.assertEqual((self.dest / "olisar.db").read_bytes(), b"DB")
        self.assertEqual(sorted(p.name for p in kb.iterdir()), ["manual.pdf"])
        self.assertEqual((kb / "manual.pdf").read_bytes(), b"PDF")
        self.assertFalse((self.root / "move" / "escaped.txt").exists())
        self.assertFalse((self.root / "absolute.txt").exists())
        self.assertEqual(self.symlinks(), [])
        self.assertEqual(self.secret.read_text(), "PRIVATE KEY")

    async def test_a_linked_database_file_comes_back_as_a_plain_file(self) -> None:
        await self.export({
            "olisar.db": _Node("file", b"DB"),
            "olisar.db-wal": _Node("link", target=str(self.secret)),
        })
        wal = self.dest / "olisar.db-wal"
        self.assertFalse(wal.is_symlink())
        self.assertNotEqual(wal.read_bytes(), b"PRIVATE KEY")
        self.assertEqual(self.symlinks(), [])

    async def test_a_linked_documents_directory_is_not_followed(self) -> None:
        await self.export({
            "olisar.db": _Node("file", b"DB"),
            "kb_uploads": _Node("link", target="/etc"),
        })
        self.assertFalse((self.dest / "kb_uploads").exists())


class LocalCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.secret = self.root / "id_ed25519"
        self.secret.write_text("PRIVATE KEY")

    def test_copy_skips_symlinks_and_replaces_ones_in_the_way(self) -> None:
        src, dest = self.root / "staged", self.root / "kb_uploads"
        src.mkdir()
        dest.mkdir()
        (src / "manual.pdf").write_bytes(b"PDF")
        (src / "key.pdf").symlink_to(self.secret)
        (dest / "manual.pdf").symlink_to(self.secret)  # left by an older move
        migrate._copy_kb(str(src), dest)
        self.assertFalse((dest / "key.pdf").exists())
        self.assertFalse((dest / "manual.pdf").is_symlink())
        self.assertEqual((dest / "manual.pdf").read_bytes(), b"PDF")
        self.assertEqual(self.secret.read_text(), "PRIVATE KEY")

    def test_staging_skips_a_document_that_is_a_symlink(self) -> None:
        kb = self.root / "kb_uploads"
        kb.mkdir()
        (kb / "real.pdf").write_bytes(b"PDF")
        (kb / "key.pdf").symlink_to(self.secret)
        db = self.root / "olisar.db"
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE kb_source (id INTEGER PRIMARY KEY, type TEXT, uri TEXT)")
            con.executemany("INSERT INTO kb_source (type, uri) VALUES ('doc', ?)",
                            [(str(kb / "real.pdf"),), (str(kb / "key.pdf"),)])
        con.close()
        staged = self.root / "staging"
        migrate._stage_docs(str(db), staged)
        self.assertEqual(sorted(p.name for p in staged.iterdir()), ["real.pdf"])


if __name__ == "__main__":
    unittest.main()
