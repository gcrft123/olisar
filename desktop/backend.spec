# PyInstaller spec for the unified Olisar backend (API + bot + dashboard).
#
# Build from the repo root:  uv run pyinstaller desktop/backend.spec
# Produces a one-folder bundle at dist/olisar-backend/ that the Electron app
# spawns as a sidecar (`olisar-backend --port <p>`), with OLISAR_DATA_DIR set.
#
# One-folder (COLLECT) not one-file: faster start, fewer AV false-positives, and it
# keeps the native sqlite-vec library and the bundled dashboard easy to ship.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # desktop/ -> repo root

binaries = []
datas = []
hiddenimports = []

# sqlite-vec (#1 packaging risk): the extension binary is loaded at runtime via
# sqlite_vec.loadable_path(), so PyInstaller's import analysis never sees it.
binaries += collect_dynamic_libs("sqlite_vec")
datas += collect_data_files("sqlite_vec")

# discord.py loads cogs through importlib (bot.load_extension), invisible to analysis.
hiddenimports += [
    "bot.cogs.guilds", "bot.cogs.conversation", "bot.cogs.members", "bot.cogs.slash",
    "bot.cogs.presence", "bot.cogs.memory_worker", "bot.cogs.context_channels",
    "bot.cogs.search_index", "bot.cogs.events", "bot.cogs.self_destruct",
    "bot.cogs.star_citizen", "bot.cogs.proactive",
]
# Whole-package collects keep dynamically-referenced submodules (extensions, the
# SQLite dialect, uvicorn's loop/protocol impls) in the bundle.
hiddenimports += collect_submodules("olisar")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["aiosqlite", "sqlalchemy.dialects.sqlite"]

# Knowledge-base crawler/parsers ship data files (stoplists, configs).
for pkg in ("trafilatura", "justext", "courlan", "htmldate"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# The built dashboard, served same-origin by FastAPI in the bundle. web_dist_dir()
# resolves this to <_MEIPASS>/web_dist when frozen.
datas += [(os.path.join(ROOT, "web", "dist"), "web_dist")]

a = Analysis(
    [os.path.join(ROOT, "olisar", "runtime", "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="olisar-backend",
    debug=False,
    strip=False,
    upx=False,
    console=True,  # logs to stdout; Electron captures it
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="olisar-backend",
)
