# Builds ../index.html from this folder: the styles, the engine and the replay, inlined with
# two images. Run it from anywhere: python3 design/memory-replay/src/assemble.py
import base64, pathlib
B = pathlib.Path(__file__).resolve().parent
ROOT = B.parents[2]
out = B.parent / 'index.html'
css = (B / 'style.css').read_text()
engine = (B / 'engine.js').read_text()
replay = (B / 'replay.js').read_text()
b64 = lambda p: base64.b64encode(p.read_bytes()).decode()
logo = b64(B / 'assets' / 'logo.png')
bot = b64(B / 'assets' / 'olisar-avatar.jpg')
page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Replay · Hollow Pines</title>
<link rel="icon" type="image/png" href="data:image/png;base64,{logo}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400..700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{css}</style>
</head>
<body>
<noscript><p class="noscript">The replay needs JavaScript.</p></noscript>
<canvas id="form" aria-hidden="true"></canvas>
<canvas id="fx" aria-hidden="true"></canvas>
<div class="orb-css" aria-hidden="true"></div>
<div class="orb-face" aria-hidden="true"></div>
<div class="spark" aria-hidden="true"></div>
<div class="catch" aria-hidden="true"></div>

<header class="hud">
  <div class="hud-name">Hollow Pines <span class="tag">Example</span></div>
  <div class="hud-actions">
    <button class="ghost icon ctl-auto" type="button" aria-label="Autoplay" data-tip="Autoplay" aria-pressed="false"></button>
    <button class="ghost icon ctl-close" type="button" aria-label="Close the replay" data-tip="Close"></button>
  </div>
</header>

<div class="narr" aria-live="polite">
  <div class="n-title"></div>
  <div class="n-body"></div>
  <div class="n-extra"></div>
  <div class="n-foot"></div>
</div>
<ul class="mems" aria-label="Memories"></ul>
<div class="mem-open" role="dialog" aria-label="Memory">
  <button class="mem-back ghost icon" type="button" aria-label="Back to the replay" data-tip="Back"></button>
  <div class="mo-in"></div>
</div>
<div class="center">
  <p class="center-line"></p>
  <p class="center-sub" hidden></p>
  <form hidden><input type="text" placeholder="Something from 2026" aria-label="What should I remember from this year?" autocomplete="off" maxlength="120" dir="auto"><button type="submit">Save</button></form>
  <div class="actions" hidden><button type="button" class="watch-again">Watch again</button><button type="button" class="primary done">Done</button></div>
</div>

<p class="hint"></p>
<nav class="timeline" aria-label="Chapters"></nav>

<section class="under start" aria-label="Start a replay">
  <h2>Replay Hollow Pines</h2>
  <p class="meta"><span class="num">412,880</span> messages in <span class="num">18</span> indexed channels</p>
  <div class="actions"><button type="button" class="primary start-go">Start</button></div>
  <p class="note">About 25 minutes and a third of today’s Gemini allowance.</p>
  <p class="note">Channels that aren’t indexed are left out.</p>
</section>
<div class="under remember">
  <h2>Remembering Hollow Pines</h2>
  <ol class="steps"><li>Gathering</li><li>Counting</li><li>Reading</li><li>Writing</li></ol>
  <p class="live"></p>
  <p class="note rem-note" hidden></p>
  <div class="actions" hidden><button type="button" class="primary rem-retry">Try again</button></div>
  <p class="rem-status visually-hidden" role="status"></p>
</div>
<section class="under play" aria-label="Play the replay">
  <h2>Hollow Pines, 2021 to now</h2>
  <p class="meta"></p>
  <div class="actions"><button type="button" class="primary play-go">Play</button></div>
</section>

<div class="demo"><span class="eyebrow">Demo</span><button class="demo-restart" type="button">Restart</button><button class="demo-skip" type="button">Skip to replay</button></div>
<div class="toast" role="status"></div>

<script>window.ASSETS = {{ bot: 'data:image/jpeg;base64,{bot}' }}</script>
<script>
{engine}</script>
<script>
{replay}</script>
</body>
</html>
'''
out.write_text(page)
print(out.relative_to(ROOT), len(page))
