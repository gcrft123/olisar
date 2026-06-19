"""Desktop-app runtime: the unified entry point that runs the API (serving the
built dashboard) and the Discord bot on a single asyncio loop, plus per-user data
directory resolution. Used by ``python -m olisar.runtime`` and the PyInstaller
backend bundled inside the Electron app.
"""
