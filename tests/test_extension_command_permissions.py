"""A slash command an extension limits to server managers is limited to them in Discord.

Run:  uv run python -m unittest tests.test_extension_command_permissions -v

The SDK lets a command declare ``defaultMemberPermissions: "manage_guild"``, and the tags and
gameservers extensions do for the commands that change a server's data (/tagset, /tagdelete,
/serverset). The bot built every command without it, so Discord offered those commands to
every member, and anyone could rewrite or delete a server's tags. Now the declared permission
becomes the command's default member permissions, and a value the bot doesn't recognize limits
the command to server managers instead of opening it up.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import discord
from discord.ext import commands

from bot.cogs.sdk_commands import _make_command
from olisar import sandbox
from olisar.sandbox import transpile

REPO = Path(__file__).resolve().parents[1]


def _command(**extra) -> dict:
    return {"name": "tagset", "description": "Save a tag.", **extra}


class DefaultPermissionTests(unittest.TestCase):
    def test_manage_guild_is_sent_to_discord(self) -> None:
        cmd = _make_command("tags", _command(defaultMemberPermissions="manage_guild"))
        self.assertTrue(cmd.default_permissions.manage_guild)
        tree = commands.Bot(command_prefix="!", intents=discord.Intents.none()).tree
        self.assertEqual(
            cmd.to_dict(tree)["default_member_permissions"],
            discord.Permissions(manage_guild=True).value,
        )

    def test_null_leaves_the_command_open_to_everyone(self) -> None:
        for value in (None, ""):
            with self.subTest(value=value):
                cmd = _make_command("tags", _command(defaultMemberPermissions=value))
                self.assertIsNone(cmd.default_permissions)
        self.assertIsNone(_make_command("tags", _command()).default_permissions)

    def test_other_permission_names_work_too(self) -> None:
        cmd = _make_command("mod", _command(defaultMemberPermissions="manage_messages"))
        self.assertTrue(cmd.default_permissions.manage_messages)
        self.assertFalse(cmd.default_permissions.manage_guild)

    def test_an_unknown_value_limits_the_command_to_server_managers(self) -> None:
        for value in ("server_owner", "everyone", "{}"):
            with self.subTest(value=value):
                cmd = _make_command("tags", _command(defaultMemberPermissions=value))
                self.assertIsNotNone(cmd.default_permissions)
                self.assertTrue(cmd.default_permissions.manage_guild)


class ShippedExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def _commands(self, filename: str) -> dict[str, dict]:
        source = (REPO / "marketplace-extensions" / filename).read_text(encoding="utf-8")
        manifest = await sandbox.extract_manifest(await transpile.transpile(source))
        return {c["name"]: c for c in manifest["commands"]}

    async def test_tags_editing_commands_are_for_server_managers(self) -> None:
        cmds = await self._commands("tags.js")
        for name in ("tagset", "tagdelete"):
            with self.subTest(command=name):
                self.assertTrue(_make_command("tags", cmds[name]).default_permissions.manage_guild)
        # Reading a tag stays open to everyone.
        self.assertIsNone(_make_command("tags", cmds["tag"]).default_permissions)

    async def test_gameservers_serverset_is_for_server_managers(self) -> None:
        cmds = await self._commands("gameservers.js")
        self.assertTrue(_make_command("gameservers", cmds["serverset"]).default_permissions.manage_guild)


if __name__ == "__main__":
    unittest.main()
