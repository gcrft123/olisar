# PyInstaller spec for the unified Olisar backend (API + bot + dashboard).
#
# Build from the repo root:  uv run pyinstaller desktop/backend.spec
# Produces a one-folder bundle at dist/olisar-backend/ that the Electron app
# spawns as a sidecar (`olisar-backend --port <p>`), with OLISAR_DATA_DIR set.
#
# One-folder (COLLECT) not one-file: faster start, fewer AV false-positives, and it
# keeps the native sqlite-vec library and the bundled dashboard easy to ship.

import glob
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # desktop/ -> repo root


def _datafiles(rel_dir, patterns, dest):
    """Collect data files by path (src, dest), failing loudly if none match.

    Used for data files in our *local* source packages: PyInstaller's
    collect_data_files() can't resolve a non-installed package in its isolated
    build subprocess (it warns "... is not a package" and silently skips), which
    once shipped a backend with no built-in extensions or sandbox assets at all.
    Globbing by path sidesteps that, and the guard turns a future miss into a
    build failure instead of a broken bundle.
    """
    found = []
    for pat in patterns:
        found += [(src, dest) for src in glob.glob(os.path.join(ROOT, rel_dir, pat))]
    if not found:
        raise SystemExit(f"backend.spec: no data files matched {rel_dir} {patterns}")
    return found

binaries = []
datas = []
hiddenimports = []

# sqlite-vec (#1 packaging risk): the extension binary is loaded at runtime via
# sqlite_vec.loadable_path(), so PyInstaller's import analysis never sees it.
binaries += collect_dynamic_libs("sqlite_vec")
datas += collect_data_files("sqlite_vec")

# quickjs (#2 packaging risk): the C-extension that runs sandboxed SDK extensions.
# A failed bundle is surfaced by the sandbox self-check on /api/health.
binaries += collect_dynamic_libs("quickjs")
hiddenimports += ["quickjs"]

# cryptography: Ed25519 signing/verification of .olx bundles. It loads a Rust native
# module dynamically; collect its submodules + libs so the frozen build can sign/verify.
# A failed bundle is surfaced by the signing self-check on /api/health.
binaries += collect_dynamic_libs("cryptography")
hiddenimports += collect_submodules("cryptography")

# The sandbox's JS bootstrap + the SDK type defs + the built-in extensions are data
# files (collect_submodules only grabs .py), so ship them explicitly by path (see
# _datafiles — collect_data_files silently skips these local packages). The vendored
# TypeScript compiler (vendor/typescript.js, ~9MB) is the server-side transpiler that
# turns extension source into the JS we run — ship it too (it lives in a subdir).
datas += _datafiles("olisar/sandbox", ["*.js", "*.d.ts"], "olisar/sandbox")
datas += _datafiles("olisar/sandbox/vendor", ["*.js"], "olisar/sandbox/vendor")
datas += _datafiles("olisar/extensions/sdk_builtins", ["*.js"], "olisar/extensions/sdk_builtins")

# discord.py loads cogs via importlib (bot.load_extension), invisible to static
# analysis — so every cog must be a hidden import. Derive the list from the files on
# disk so a newly-added cog can't be silently dropped from the bundle (a hand-kept
# list once missed bot.cogs.reminders and bot.cogs.welcome, crashing the bot).
_cog_mods = [
    "bot.cogs." + os.path.splitext(os.path.basename(f))[0]
    for f in sorted(glob.glob(os.path.join(ROOT, "bot", "cogs", "*.py")))
    if not os.path.basename(f).startswith("__")
]
if not _cog_mods:
    raise SystemExit("backend.spec: found no bot.cogs modules to bundle")
hiddenimports += _cog_mods
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
