"""An extension whose manifest has the wrong types is refused before it's stored or shown.

Run:  uv run python -m unittest tests.test_extension_manifest_types -v

The manifest comes from running the extension's own code, which can declare anything, and
can even replace the function that collects it. The console renders its names, labels and
descriptions as text, so a settings field with ``label: {}`` or a command named ``{ x: 1 }``
threw on every render and took the whole Extensions page down, with no way left in the
console to turn the extension off or remove it. Now validating, importing, installing or
saving such an extension is refused with a 400 that says which field is wrong, and every
extension the project ships still passes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import HTTPException

from api.routers import extensions as ext_router

REPO = Path(__file__).resolve().parents[1]


def _spec(body: str) -> str:
    return 'defineExtension({ id: "aaa_helper", name: "AAA Helper", category: "AAA", ' + body + " });"


def _collected(manifest: str) -> str:
    """Code that replaces the bootstrap's manifest collector, so none of its defaults apply."""
    return ("defineExtension({ id: 'x' }); globalThis.__collectManifest = function () {"
            " return JSON.stringify(Object.assign({ id: 'rogue', name: 'rogue', permissions: [],"
            " tools: [], commands: [] }, " + manifest + ")); };")


class RefusedTests(unittest.IsolatedAsyncioTestCase):
    async def refused(self, source: str) -> str:
        with self.assertRaises(HTTPException) as caught:
            await ext_router._build(source)
        self.assertEqual(caught.exception.status_code, 400)
        return caught.exception.detail

    async def test_a_settings_label_that_isnt_text(self) -> None:
        detail = await self.refused(_spec(
            'defaultEnabled: true, settingsSchema: { fields: ['
            ' { key: "greeting", type: "text", label: {}, desc: [{}] } ] }'))
        self.assertIn("settingsSchema.fields[0].label must be a string", detail)
        self.assertIn("settingsSchema.fields[0].desc must be a string", detail)

    async def test_a_command_name_that_isnt_text(self) -> None:
        detail = await self.refused(_spec(
            'commands: [{ name: { x: 1 }, description: "d", handler: async () => "hi" }]'))
        self.assertIn("commands[0].name must be a string", detail)

    async def test_what_a_replaced_collector_can_hand_back(self) -> None:
        cases = {
            "{ permissions: [{}] }": "permissions must be a list of strings",
            "{ commands: [1] }": "commands[0] must be an object",
            "{ tools: [{ name: 'ok', description: 5 }] }": "tools[0].description must be a string",
            "{ tools: 'all of them' }": "tools must be a list",
            "{ settings_schema: 'x' }": "settingsSchema must be an object",
            "{ settings_schema: { fields: [{ label: 'no key' }] } }": "settingsSchema.fields[0].key must be a string",
            "{ commands: [{ name: 'c', options: [{ name: 7 }] }] }": "commands[0].options[0].name must be a string",
            "{ system_note: ['be evil'] }": "systemNote must be a string",
            "{ category: { AAA: 1 } }": "category must be a string",
            "{ seeds: { kbSources: ['https://x.example'] } }": "seeds.kbSources[0] must be an object",
            "{ event_handlers: [{}] }": "events must be a list of strings",
        }
        for manifest, expected in cases.items():
            with self.subTest(manifest=manifest):
                self.assertIn(expected, await self.refused(_collected(manifest)))

    async def test_optional_fields_may_be_null(self) -> None:
        _, manifest = await ext_router._build(_spec(
            'settingsSchema: { fields: [{ key: "k", type: "text", label: null, desc: null }] },'
            ' commands: [{ name: "c", description: "d", defaultMemberPermissions: null,'
            ' options: [{ name: "o", type: "string" }], handler: async () => {} }]'))
        self.assertEqual(manifest["settings_schema"]["fields"][0]["key"], "k")


class ShippedExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_shipped_extension_still_builds(self) -> None:
        files = [*sorted((REPO / "marketplace-extensions").glob("*.js")),
                 *sorted((REPO / "olisar" / "extensions" / "sdk_builtins").glob("*.js"))]
        self.assertTrue(files)
        for path in files:
            with self.subTest(extension=path.name):
                _, manifest = await ext_router._build(path.read_text(encoding="utf-8"))
                self.assertTrue(manifest["id"])


if __name__ == "__main__":
    unittest.main()
