#!/usr/bin/env node
// Rebuild public docs from the in-app source of truth.
//
// Canonical content:
//   - web/src/docs.tsx          → DOCS + DOC_GROUPS (in-app console)
//   - DOCUMENTATION.md Setup    → install / Discord app / wizard / from-source
//     (site-only; not shown in the in-app Docs tab)
//
// Writes:
//   - docs/docs.html            → GitHub Pages docs site
//   - DOCUMENTATION.md          → consolidated markdown (in-app + setup)
//
//   node web/scripts/build-docs-site.mjs
//
// Run after editing docs.tsx (or the Setup section of DOCUMENTATION.md) so Pages
// and the markdown mirror stay in lockstep with the console.

import { transformSync } from 'esbuild'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webDir = resolve(here, '..')
const repoRoot = resolve(webDir, '..')
const docsHtmlPath = resolve(repoRoot, 'docs', 'docs.html')
const mdPath = resolve(repoRoot, 'DOCUMENTATION.md')

// ── Load DOCS / DOC_GROUPS from the console source ────────────────────────────
const tsx = readFileSync(resolve(webDir, 'src', 'docs.tsx'), 'utf8')
const js = transformSync(tsx, { loader: 'tsx', format: 'esm' }).code
const tmpModule = resolve('/tmp', '_olisar_docs_site.mjs')
writeFileSync(tmpModule, js)
const { DOCS, DOC_GROUPS } = await import(pathToFileURL(tmpModule).href + `?t=${Date.now()}`)

// ── Setup sections (site + DOCUMENTATION.md only) ────────────────────────────
// Parsed from the existing DOCUMENTATION.md so setup stays editable there without
// polluting the in-app Docs tab. Fall back to a short stub if missing.
const SETUP_IDS = [
  { id: 'install', title: 'Install the desktop app', match: /^### Install the desktop app\s*$/m },
  { id: 'discord-app', title: 'Create your Discord application', match: /^### Create your Discord application\s*$/m },
  { id: 'wizard', title: 'First-run setup wizard', match: /^### First-run setup wizard\s*$/m },
  { id: 'from-source', title: 'Build & run from source', match: /^### Build & run from source\s*$/m },
]

function extractSetupSections(mdText) {
  const start = mdText.search(/^## Setup\s*$/m)
  if (start < 0) return []
  const rest = mdText.slice(start)
  const endRel = rest.slice(1).search(/^## /m)
  const setupBlock = endRel < 0 ? rest : rest.slice(0, endRel + 1)
  const sections = []
  for (let i = 0; i < SETUP_IDS.length; i++) {
    const cur = SETUP_IDS[i]
    const m = cur.match.exec(setupBlock)
    if (!m) continue
    const bodyStart = m.index + m[0].length
    let bodyEnd = setupBlock.length
    for (let j = i + 1; j < SETUP_IDS.length; j++) {
      const n = SETUP_IDS[j].match.exec(setupBlock)
      if (n) { bodyEnd = n.index; break }
    }
    // Also stop at a following ## if any leftover.
    const after = setupBlock.slice(bodyStart, bodyEnd)
    const nextH2 = after.search(/\n## /)
    const body = (nextH2 >= 0 ? after.slice(0, nextH2) : after).trim()
    sections.push({ id: cur.id, title: cur.title, body })
  }
  return sections
}

const prevMd = readFileSync(mdPath, 'utf8')
const setupSections = extractSetupSections(prevMd)
if (setupSections.length !== SETUP_IDS.length) {
  console.warn(
    `warn: expected ${SETUP_IDS.length} Setup sections in DOCUMENTATION.md, found ${setupSections.length}. ` +
    'Preserving what we found; fill missing ### headings under ## Setup.',
  )
}

// Site nav: in-app groups with a Setup group inserted after Start.
const SITE_GROUPS = []
for (const g of DOC_GROUPS) {
  SITE_GROUPS.push(g)
  if (g.label === 'Start' && setupSections.length) {
    SITE_GROUPS.push({ label: 'Setup', ids: setupSections.map((s) => s.id) })
  }
}

const allDocs = [...DOCS, ...setupSections]
const byId = Object.fromEntries(allDocs.map((s) => [s.id, s]))
const ordered = SITE_GROUPS.flatMap((g) => g.ids.map((id) => byId[id]).filter(Boolean))

// ── Markdown → HTML (mirrors the console / existing docs.html conventions) ───
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function slugify(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

function inline(text) {
  let out = ''
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m
  while ((m = re.exec(text))) {
    if (m.index > last) out += esc(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('**')) out += `<strong>${esc(t.slice(2, -2))}</strong>`
    else if (t.startsWith('`')) out += `<code>${esc(t.slice(1, -1))}</code>`
    else {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)
      const label = esc(mm[1])
      const url = mm[2]
      if (url.startsWith('tab:')) {
        // Dashboard tabs have no target on the public site — plain text.
        out += label
      } else if (url.startsWith('#')) {
        const id = url.slice(1).split(/[/?#]/)[0]
        // Link to a docs section when the hash is a known id; otherwise keep as page anchor.
        if (byId[id]) out += `<a href="#${esc(id)}" data-doc="${esc(id)}">${label}</a>`
        else out += `<a href="${esc(url)}">${label}</a>`
      } else {
        out += `<a href="${esc(url)}" target="_blank" rel="noreferrer">${label}</a>`
      }
    }
    last = m.index + t.length
  }
  if (last < text.length) out += esc(text.slice(last))
  return out
}

const CALLOUT_LABELS = { tip: 'Tip', note: 'Note', warning: 'Warning', info: 'Info' }
const splitRow = (l) => l.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
const isTableSep = (l) => /^\|?[\s:|-]+\|?$/.test(l.trim()) && l.includes('-') && l.includes('|')

function renderBlocks(rawLines) {
  const lines = rawLines.map((l) => l.replace(/\r$/, ''))
  let out = ''
  let list = []
  let olist = []
  let para = []
  const flushList = () => {
    if (list.length) {
      out += '<ul>' + list.map((li) => `<li>${inline(li)}</li>`).join('') + '</ul>'
      list = []
    }
  }
  const flushOList = () => {
    if (olist.length) {
      out += '<ol>' + olist.map((li) => `<li>${inline(li)}</li>`).join('') + '</ol>'
      olist = []
    }
  }
  const flushPara = () => {
    if (para.length) {
      out += `<p>${inline(para.join(' '))}</p>`
      para = []
    }
  }
  const flushAll = () => { flushList(); flushOList(); flushPara() }

  let i = 0
  while (i < lines.length) {
    const raw = lines[i]
    const line = raw.trim()

    // Fenced code
    const fence = line.match(/^```(\w*)\s*$/)
    if (fence) {
      flushAll()
      const lang = fence[1] || ''
      const body = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i])
        i++
      }
      i++ // closing fence
      out += `<pre class="doc-pre"><code>${esc(body.join('\n'))}</code></pre>`
      continue
    }

    const cm = line.match(/^:::(tip|note|warning|info)\s*(.*)$/)
    if (cm) {
      flushAll()
      const inner = []
      i++
      while (i < lines.length && lines[i].trim() !== ':::') {
        inner.push(lines[i])
        i++
      }
      i++ // closing :::
      const label = cm[2].trim() || CALLOUT_LABELS[cm[1]]
      out += `<div class="callout callout-${cm[1]}"><div class="callout-label">${esc(label)}</div>`
        + `<div class="callout-body">${renderBlocks(inner)}</div></div>`
      continue
    }

    if (line.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushAll()
      const header = splitRow(line)
      i += 2
      const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i].trim()))
        i++
      }
      out += '<div class="doc-table-wrap"><table class="doc-table"><thead><tr>'
        + header.map((h) => `<th>${inline(h)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('')
        + '</tbody></table></div>'
      continue
    }

    if (!line) { flushAll(); i++; continue }

    if (line.startsWith('#### ')) {
      flushAll()
      const t = line.slice(5)
      out += `<h4 id="${esc(slugify(t))}">${inline(t)}</h4>`
      i++; continue
    }
    if (line.startsWith('### ')) {
      flushAll()
      const t = line.slice(4)
      out += `<h3 id="${esc(slugify(t))}">${inline(t)}</h3>`
      i++; continue
    }
    if (line.startsWith('## ')) {
      flushAll()
      const t = line.slice(3)
      // Public site uses h3 for section subheads (matches prior docs.html).
      out += `<h3 id="${esc(slugify(t))}">${inline(t)}</h3>`
      i++; continue
    }

    const ol = line.match(/^(\d+)\.\s+(.*)$/)
    if (ol) {
      flushList(); flushPara()
      olist.push(ol[2])
      i++; continue
    }
    if (line.startsWith('- ')) {
      flushOList(); flushPara()
      list.push(line.slice(2))
      i++; continue
    }

    // Continuation of list item
    if (list.length && raw.match(/^\s+\S/)) {
      list[list.length - 1] += ' ' + line
      i++; continue
    }
    if (olist.length && raw.match(/^\s+\S/)) {
      olist[olist.length - 1] += ' ' + line
      i++; continue
    }

    if (list.length || olist.length) {
      // New paragraph after list
      flushAll()
    }
    para.push(line)
    i++
  }
  flushAll()
  return out
}

// ── docs/docs.html ───────────────────────────────────────────────────────────
const prevHtml = readFileSync(docsHtmlPath, 'utf8')
const headEnd = prevHtml.indexOf('<div class="docs-shell">')
if (headEnd < 0) throw new Error('docs/docs.html: missing <div class="docs-shell">')
const head = prevHtml.slice(0, headEnd)
const upgradeStart = prevHtml.indexOf('<script>\n/* Upgrade plain doc code blocks')
if (upgradeStart < 0) throw new Error('docs/docs.html: missing code-upgrade script')
const upgradeScript = prevHtml.slice(upgradeStart)

const navHtml = SITE_GROUPS.map((g) => {
  const items = g.ids.map((id) => byId[id]).filter(Boolean)
  if (!items.length) return ''
  return `<div class="docs-group"><div class="docs-nav-label">${esc(g.label)}</div>`
    + items.map((s) => `<div class="docs-nav-item" data-id="${esc(s.id)}">${esc(s.title)}</div>`).join('')
    + '</div>'
}).join('')

const articles = ordered.map((s, idx) => {
  const hidden = idx === 0 ? '' : ' hidden'
  const body = renderBlocks(s.body.trim().split('\n'))
  return `<article class="doc-section" data-id="${esc(s.id)}"${hidden}>`
    + `<h1 class="docs-title">${esc(s.title)}</h1>`
    + `<div class="doc">${body}</div></article>`
}).join('')

const orderJson = JSON.stringify(ordered.map((s) => s.id))
const titlesObj = Object.fromEntries(ordered.map((s) => [s.id, s.title]))
const titlesJson = JSON.stringify(titlesObj)

const navScript = `<script>const ORDER=${orderJson};const TITLES=${titlesJson};
function setNavH(){document.documentElement.style.setProperty('--navh',document.querySelector('nav').offsetHeight+'px');}
setNavH();window.addEventListener('resize',setNavH);
var shell=document.querySelector('.docs-shell');
var nav=document.querySelector('.docs-nav');
var content=document.querySelector('.docs-content');
var tocAside=document.querySelector('.docs-toc');
var tocLinks=document.getElementById('tocLinks');
var search=document.querySelector('.docs-search');
var prevBtn=document.getElementById('docPrev');
var nextBtn=document.getElementById('docNext');
function sectionEl(id){return content.querySelector('.doc-section[data-id="'+id+'"]');}
function buildTOC(sec){var hs=[].slice.call(sec.querySelectorAll('h3,h4'));tocLinks.innerHTML='';if(!hs.length){tocAside.style.display='none';shell.classList.add('no-toc');return;}tocAside.style.display='';shell.classList.remove('no-toc');hs.forEach(function(h){var a=document.createElement('a');a.textContent=h.textContent;a.className=(h.tagName==='H4'?'lvl2':'lvl1');a.href='#';a.addEventListener('click',function(e){e.preventDefault();h.scrollIntoView({behavior:'smooth',block:'start'});});tocLinks.appendChild(a);});}
function setBtn(btn,id,pre,post){if(id){btn.style.visibility='visible';btn.textContent=pre+TITLES[id]+post;btn.dataset.id=id;}else{btn.style.visibility='hidden';btn.removeAttribute('data-id');}}
function activate(id,push){if(!sectionEl(id))id=ORDER[0];var secs=content.querySelectorAll('.doc-section');[].forEach.call(secs,function(s){s.hidden=s.dataset.id!==id;});[].forEach.call(nav.querySelectorAll('.docs-nav-item'),function(n){n.classList.toggle('active',n.dataset.id===id);});var sec=sectionEl(id);buildTOC(sec);var oi=ORDER.indexOf(id);setBtn(prevBtn,ORDER[oi-1],'\\u2190 ','');setBtn(nextBtn,ORDER[oi+1],'',' \\u2192');window.scrollTo({top:0});document.title=TITLES[id]+' \\u00b7 Olisar docs';if(push&&location.hash!=='#'+id)history.pushState(null,'','#'+id);}
nav.addEventListener('click',function(e){var it=e.target.closest('.docs-nav-item');if(it)activate(it.dataset.id,true);});
[prevBtn,nextBtn].forEach(function(b){b.addEventListener('click',function(){if(b.dataset.id)activate(b.dataset.id,true);});});
content.addEventListener('click',function(e){var a=e.target.closest('a[data-doc]');if(a){e.preventDefault();activate(a.getAttribute('data-doc'),true);}});
search.addEventListener('input',function(){var term=search.value.trim().toLowerCase();[].forEach.call(nav.querySelectorAll('.docs-group'),function(g){var any=false;[].forEach.call(g.querySelectorAll('.docs-nav-item'),function(it){var sec=sectionEl(it.dataset.id);var hay=(it.textContent+' '+(sec?sec.textContent:'')).toLowerCase();var show=!term||hay.indexOf(term)>=0;it.style.display=show?'':'none';if(show)any=true;});g.style.display=any?'':'none';});});
window.addEventListener('hashchange',function(){var id=(location.hash||'').replace(/^#/,'');if(ORDER.indexOf(id)>=0)activate(id,false);});
var initial=(location.hash||'').replace(/^#/,'');activate(ORDER.indexOf(initial)>=0?initial:ORDER[0],false);</script>
`

const newHtml = head
  + `<div class="docs-shell">\n`
  + `<aside class="docs-nav"><input class="docs-search" type="text" placeholder="Search docs…" />${navHtml}</aside>\n`
  + `<main class="docs-content">${articles}`
  + `<div class="docs-prevnext"><button class="ghost" id="docPrev"></button><button class="ghost" id="docNext"></button></div></main>\n`
  + `<aside class="docs-toc"><div class="docs-toc-label">On this page</div><div id="tocLinks"></div></aside>\n`
  + `</div>\n`
  + navScript
  + upgradeScript

writeFileSync(docsHtmlPath, newHtml)
console.log(`wrote docs/docs.html (${ordered.length} sections)`)

// ── DOCUMENTATION.md ─────────────────────────────────────────────────────────
function ghCallout(kind, title, bodyLines) {
  // GitHub alert: > [!TIP] etc. Title line is bold first sentence of body when custom.
  const tag = { tip: 'TIP', note: 'NOTE', warning: 'WARNING', info: 'NOTE' }[kind] || 'NOTE'
  const body = bodyLines.map((l) => l.replace(/^\s+/, '')).join('\n').trim()
  const lines = body.split('\n')
  let out = `> [!${tag}]\n`
  if (title && title !== CALLOUT_LABELS[kind]) {
    out += `> **${title}**\n`
  }
  for (const l of lines) out += `> ${l}\n`
  return out.trimEnd() + '\n\n'
}

function mdSlug(title) {
  return title.toLowerCase()
    .replace(/&/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
}

function rewriteMdLinks(line) {
  // tab: links are dashboard-only → plain text. #doc-id → GitHub heading slug.
  return line
    .replace(/\[([^\]]+)\]\(tab:[^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\(#([a-z0-9_-]+)\)/g, (_, label, id) => {
      const sec = byId[id]
      if (sec) return `[${label}](#${mdSlug(sec.title)})`
      return `[${label}](#${id})`
    })
}

function mdFromDocBody(body) {
  // docs.tsx uses ::: callouts and ##/### subheads under a page title. For
  // DOCUMENTATION.md each page is already a ###, so demote body headings one step
  // and convert callouts to GitHub alerts.
  const lines = body.trim().split('\n')
  let out = ''
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const cm = line.trim().match(/^:::(tip|note|warning|info)\s*(.*)$/)
    if (cm) {
      const title = cm[2].trim()
      const inner = []
      i++
      while (i < lines.length && lines[i].trim() !== ':::') {
        inner.push(rewriteMdLinks(lines[i]))
        i++
      }
      i++
      out += ghCallout(cm[1], title, inner)
      continue
    }
    let l = rewriteMdLinks(line)
    if (l.startsWith('#### ')) l = '##### ' + l.slice(5)
    else if (l.startsWith('### ')) l = '#### ' + l.slice(4)
    else if (l.startsWith('## ')) l = '#### ' + l.slice(3)
    out += l + '\n'
    i++
  }
  return out.trimEnd() + '\n'
}

// TOC from site groups (includes Setup).
let toc = ''
for (const g of SITE_GROUPS) {
  const items = g.ids.map((id) => byId[id]).filter(Boolean)
  if (!items.length) continue
  toc += `**${g.label}**\n\n`
  for (const s of items) toc += `- [${s.title}](#${mdSlug(s.title)})\n`
  toc += '\n'
}

const intro = `# Olisar documentation

Olisar is a **self-hosted AI Discord bot** that feels like a member of your server — it reads the
channels you allow, remembers context, builds a sense of who people are, and chimes in with its own
personality. You run **one desktop app** on your own machine; it hosts the bot for your Discord
server(s) and serves the admin console, and everything it knows stays **local**. Each install uses
your own Discord bot and your own **free** API keys (Google Gemini, and optionally Cloudflare) — so
there's no server to rent and no cloud.

This is the complete documentation: the same content as the in-app **Docs**, plus the full setup
guide. New here? Read [What Olisar is](#what-olisar-is), then jump to [Setup](#setup) to get running.

> [!NOTE]
> This document is **generated** from [web/src/docs.tsx](web/src/docs.tsx) (in-app Docs) and the Setup
> sections below. Edit those sources, then run \`node web/scripts/build-docs-site.mjs\` to refresh
> this file and the GitHub Pages site in \`docs/docs.html\`.

## Contents

${toc}`

// Body: walk SITE_GROUPS. Setup is ## Setup with #### subsections; others are ### under ## group.
let body = ''
for (const g of SITE_GROUPS) {
  const items = g.ids.map((id) => byId[id]).filter(Boolean)
  if (!items.length) continue
  body += `## ${g.label}\n\n`
  for (const s of items) {
    body += `### ${s.title}\n\n`
    body += mdFromDocBody(s.body) + '\n'
  }
}

writeFileSync(mdPath, intro + body)
console.log(`wrote DOCUMENTATION.md (${ordered.length} sections)`)

// Sanity: every in-app id present
const missing = DOCS.filter((d) => !ordered.find((s) => s.id === d.id))
if (missing.length) {
  console.warn('warn: in-app sections missing from site order:', missing.map((m) => m.id).join(', '))
}
console.log('OK — docs instances regenerated from docs.tsx')
