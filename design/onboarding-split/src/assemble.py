# Builds ../index.html from this folder: the styles, the engine, the final screen's controller and
# the wizard, inlined with the vendored Preact + htm bundle, two images, and the repo's docs site.
# Run it from anywhere: python3 design/onboarding-split/src/assemble.py
import base64, pathlib, re
B = pathlib.Path(__file__).resolve().parent
ROOT = B.parents[2]
out = B.parent / 'index.html'
vendor = (B / 'vendor' / 'htm-preact-standalone.umd.js').read_text().strip()
css = (B / 'style.css').read_text()
engine = (B / 'engine.js').read_text()
brain = (B / 'brain.js').read_text()
app = (B / 'app.js').read_text()
b64 = lambda p: base64.b64encode(p.read_bytes()).decode()
logo = b64(B / 'assets' / 'logo.png')
bot = b64(B / 'assets' / 'olisar-avatar.jpg')
import json, re
# The docs site for the drawer: docs/docs.html without its marketing bar (whose links lead to
# pages that aren't here), its favicon, or the bar's height in the sticky offsets.
docs = (ROOT / 'docs' / 'docs.html').read_text()
docs = re.sub(r'<link rel="icon"[^>]*>\s*', '', docs, count=1)
# The bar stays in the page (its script measures it) but is hidden, and its logo, which
# would 404 here, goes.
docs = re.sub(r'(<nav><a class="brand"[^>]*>)<img [^>]*>', r'\1', docs, count=1)
docs = docs.replace('</head>', '<style>body>nav{display:none!important}:root{--navh:0px}.docs-shell{max-width:none}</style></head>', 1)
# Inside a frame the page's address is about:srcdoc, where pushState can't write '#section';
# falling back to the fragment keeps its section links working (it already listens for it).
docs = docs.replace('<head>', "<head><script>(function(){var p=history.pushState.bind(history);history.pushState=function(s,t,u){try{p(s,t,u)}catch(e){if(typeof u==='string'&&u.charAt(0)==='#')location.hash=u}}})()</script>", 1)
docs_js = json.dumps(docs).replace('<', '\\u003c')
page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Set up Olisar</title>
<link rel="icon" type="image/png" href="data:image/png;base64,{logo}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400..700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{css}</style>
</head>
<body>
<div class="onb" id="onb"></div>
<!-- Preact 10 + htm 3.1.1, from htm/preact/standalone (MIT and Apache-2.0). -->
<script>{vendor}</script>
<script>
{engine}</script>
<script>
{brain}</script>
<script>
{app}</script>
<!-- The app logo (web/public/logo.png at 128px), the mock bot's avatar, and docs/docs.html for the docs drawer. -->
<script>window.ASSETS = {{ logo: 'data:image/png;base64,{logo}', bot: 'data:image/jpeg;base64,{bot}', docs: {docs_js} }}</script>
</body>
</html>
'''
out.write_text(page)
print(out.relative_to(ROOT), len(page))
