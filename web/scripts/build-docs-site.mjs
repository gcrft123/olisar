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
// polluting the in-app Docs tab. Each ### under "## Setup" is a page, in this order of ids;
// the title is whatever the heading says.
const SETUP_IDS = ['install', 'discord-app', 'wizard', 'from-source']

function extractSetupSections(mdText) {
  const start = mdText.search(/^## Setup\s*$/m)
  if (start < 0) return []
  const rest = mdText.slice(start)
  const endRel = rest.slice(1).search(/^## /m)
  const setupBlock = endRel < 0 ? rest : rest.slice(0, endRel + 1)
  return setupBlock.split(/^### /m).slice(1, SETUP_IDS.length + 1).map((part, i) => {
    const nl = part.indexOf('\n')
    // A page's headings sit under its ### there (#### and #####); on the site they're the
    // page's own h2 and h3, as on every other page. The last page runs up to the file's `---`
    // before the next chapter, which belongs to the file, not the page.
    const body = part.slice(nl + 1).trim().replace(/\n+(?:-{3,}|\*{3,})\s*$/, '').trim()
      .replace(/^#####(?= )/gm, '###').replace(/^####(?= )/gm, '##')
    return { id: SETUP_IDS[i], title: part.slice(0, nl).trim(), body }
  })
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
// DOCUMENTATION.md links to sections by GitHub's heading anchor ("Build & run from source" is
// #build--run-from-source); on the site those name the section's id instead.
const ghSlug = (t) => String(t).toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s/g, '-')
const idByGhSlug = Object.fromEntries(allDocs.map((s) => [ghSlug(s.title), s.id]))
const ordered = SITE_GROUPS.flatMap((g) => g.ids.map((id) => byId[id]).filter(Boolean))

// ── Markdown → HTML (mirrors the console / existing docs.html conventions) ───
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function slugify(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

function inline(text) {
  let out = ''
  // <kbd>…</kbd> renders as a key chip, as in the console, instead of printing its tags.
  const re = /(<kbd>[^<]+<\/kbd>|\*\*[^*]+\*\*|\*(?=\S)[^*]+?(?<=\S)\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m
  while ((m = re.exec(text))) {
    if (m.index > last) out += esc(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('<kbd>')) out += `<kbd>${esc(t.slice(5, -6))}</kbd>`
    // Bold and italic text can hold a link or code ("**[from source](#…)**"), so their
    // contents go through inline() too instead of printing as plain text.
    else if (t.startsWith('**')) out += `<strong>${inline(t.slice(2, -2))}</strong>`
    else if (t.startsWith('*')) out += `<em>${inline(t.slice(1, -1))}</em>`
    else if (t.startsWith('`')) out += `<code>${esc(t.slice(1, -1))}</code>`
    else {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)
      const label = esc(mm[1])
      const url = mm[2]
      if (url.startsWith('tab:')) {
        // Dashboard tabs have no target on the public site — plain text.
        out += label
      } else if (url.startsWith('#')) {
        const raw = url.slice(1).split(/[/?#]/)[0]
        const id = byId[raw] ? raw : idByGhSlug[raw]
        // Link to a docs section when the hash names one; otherwise keep it as a page anchor.
        if (id) out += `<a href="#${esc(id)}" data-doc="${esc(id)}">${label}</a>`
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

// The console's callout icons (ui.tsx CALLOUT_ICON): Solar bold check-circle, info-circle and
// danger-triangle at 17px. A callout shows a title only when the source gives one, as in the console.
const svg17 = (d) => `<svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" clip-rule="evenodd" d="${d}"/></svg>`
const ICON_CHECK = svg17('M22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12ZM16.0303 8.96967C16.3232 9.26256 16.3232 9.73744 16.0303 10.0303L11.0303 15.0303C10.7374 15.3232 10.2626 15.3232 9.96967 15.0303L7.96967 13.0303C7.67678 12.7374 7.67678 12.2626 7.96967 11.9697C8.26256 11.6768 8.73744 11.6768 9.03033 11.9697L10.5 13.4393L12.7348 11.2045L14.9697 8.96967C15.2626 8.67678 15.7374 8.67678 16.0303 8.96967Z')
const ICON_INFO = svg17('M22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12ZM12 17.75C12.4142 17.75 12.75 17.4142 12.75 17V11C12.75 10.5858 12.4142 10.25 12 10.25C11.5858 10.25 11.25 10.5858 11.25 11V17C11.25 17.4142 11.5858 17.75 12 17.75ZM12 7C12.5523 7 13 7.44772 13 8C13 8.55228 12.5523 9 12 9C11.4477 9 11 8.55228 11 8C11 7.44772 11.4477 7 12 7Z')
const ICON_WARN = svg17('M5.31171 10.7615C8.23007 5.58716 9.68925 3 12 3C14.3107 3 15.7699 5.58716 18.6883 10.7615L19.0519 11.4063C21.4771 15.7061 22.6897 17.856 21.5937 19.428C20.4978 21 17.7864 21 12.3637 21H11.6363C6.21356 21 3.50217 21 2.40626 19.428C1.31034 17.856 2.52291 15.7061 4.94805 11.4063L5.31171 10.7615ZM12 7.25C12.4142 7.25 12.75 7.58579 12.75 8V13C12.75 13.4142 12.4142 13.75 12 13.75C11.5858 13.75 11.25 13.4142 11.25 13V8C11.25 7.58579 11.5858 7.25 12 7.25ZM12 17C12.5523 17 13 16.5523 13 16C13 15.4477 12.5523 15 12 15C11.4477 15 11 15.4477 11 16C11 16.5523 11.4477 17 12 17Z')
const CALLOUT_ICON = { tip: ICON_CHECK, note: ICON_INFO, info: ICON_INFO, warning: ICON_WARN }
// DOCUMENTATION.md's GitHub alerts drop a title that only repeats the alert's own label.
const CALLOUT_LABELS = { tip: 'Tip', note: 'Note', warning: 'Warning', info: 'Info' }
const splitRow = (l) => l.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
const isTableSep = (l) => /^\|?[\s:|-]+\|?$/.test(l.trim()) && l.includes('-') && l.includes('|')

function renderBlocks(rawLines) {
  // Ported from the console's renderer (ui.tsx renderBlocks) so the site and the Docs page
  // build the same document: a numbered list keeps its numbers across an interruption, a loose
  // list (items a blank line apart) stays one list, and a wrapped item joins its bullet.
  const lines = rawLines.map((l) => l.replace(/\r$/, ''))
  let out = ''
  let list = []
  let ordered = false
  let start = 1
  let para = []
  const flushList = () => {
    if (list.length) {
      const items = list.map((li) => `<li>${inline(li)}</li>`).join('')
      out += ordered ? `<ol${start !== 1 ? ` start="${start}"` : ''}>${items}</ol>` : `<ul>${items}</ul>`
      list = []
    }
  }
  const flushPara = () => {
    if (para.length) {
      out += `<p>${inline(para.join(' '))}</p>`
      para = []
    }
  }
  const flushAll = () => { flushList(); flushPara() }

  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()

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
      const title = cm[2].trim()
      out += `<div class="callout ${cm[1]}"><span class="ic">${CALLOUT_ICON[cm[1]]}</span><div class="callout-body">`
        + (title ? `<div class="callout-title">${inline(title)}</div>` : '')
        + `${renderBlocks(inner)}</div></div>`
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

    // A thematic break (---) has no counterpart in the console's renderer, so it draws nothing
    // rather than printing its dashes.
    if (/^(?:-{3,}|\*{3,})$/.test(line)) { flushAll(); i++; continue }

    if (!line) {
      if (list.length) {
        let j = i + 1
        while (j < lines.length && !lines[j].trim()) j++
        const after = (lines[j] || '').trim()
        if (ordered ? /^\d+\.\s+/.test(after) : after.startsWith('- ')) { i = j; continue }
      }
      flushAll(); i++; continue
    }

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
      // ## is h2, as in the console: the section title is the page's h1.
      out += `<h2 id="${esc(slugify(t))}">${inline(t)}</h2>`
      i++; continue
    }

    const num = /^(\d+)\.\s+(.*)$/.exec(line)
    if (num || line.startsWith('- ')) {
      flushPara()
      if (list.length && ordered !== !!num) flushList()
      if (!list.length) start = num ? Number(num[1]) : 1
      ordered = !!num
      list.push(num ? num[2] : line.slice(2))
      i++; continue
    }
    if (list.length) { list[list.length - 1] += ' ' + line; i++; continue }
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
    + items.map((s) => `<button type="button" class="docs-nav-item" data-id="${esc(s.id)}">${esc(s.title)}</button>`).join('')
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

// The console's Docs page (pages.tsx Docs), as a static page: two panes, the open section in
// the URL hash, search over titles and bodies, prev/next in sidebar order.
const ARROW_L = '<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 5L9 12L15 19"/></svg>'
const ARROW_R = '<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5L15 12L9 19"/></svg>'
const navScript = `<script>const ORDER=${orderJson};const TITLES=${titlesJson};const ARROW_L=${JSON.stringify(ARROW_L)};const ARROW_R=${JSON.stringify(ARROW_R)};
function setNavH(){document.documentElement.style.setProperty('--navh',document.querySelector('.site-nav').offsetHeight+'px');}
setNavH();window.addEventListener('resize',setNavH);
var nav=document.querySelector('.docs-nav');
var content=document.querySelector('.docs-content');
var search=document.querySelector('.docs-search');
var nofind=document.querySelector('.docs-nofind');
var prevBtn=document.getElementById('docPrev');
var nextBtn=document.getElementById('docNext');
function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function sectionEl(id){return content.querySelector('.doc-section[data-id="'+id+'"]');}
function setBtn(btn,id,left){if(id){btn.style.visibility='visible';btn.innerHTML=left?ARROW_L+' '+esc(TITLES[id]):esc(TITLES[id])+' '+ARROW_R;btn.dataset.id=id;}else{btn.style.visibility='hidden';btn.innerHTML='';btn.removeAttribute('data-id');}}
function activate(id,push){if(!sectionEl(id))id=ORDER[0];[].forEach.call(content.querySelectorAll('.doc-section'),function(s){s.hidden=s.dataset.id!==id;});[].forEach.call(nav.querySelectorAll('.docs-nav-item'),function(n){var on=n.dataset.id===id;n.classList.toggle('active',on);if(on)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});var oi=ORDER.indexOf(id);setBtn(prevBtn,ORDER[oi-1],true);setBtn(nextBtn,ORDER[oi+1],false);window.scrollTo({top:0});document.title=TITLES[id]+' · Olisar docs';if(push&&location.hash!=='#'+id)history.pushState(null,'','#'+id);}
nav.addEventListener('click',function(e){var it=e.target.closest('.docs-nav-item');if(it)activate(it.dataset.id,true);});
[prevBtn,nextBtn].forEach(function(b){b.addEventListener('click',function(){if(b.dataset.id)activate(b.dataset.id,true);});});
content.addEventListener('click',function(e){var a=e.target.closest('a[data-doc]');if(a){e.preventDefault();activate(a.getAttribute('data-doc'),true);}});
search.addEventListener('input',function(){var raw=search.value.trim(),term=raw.toLowerCase(),total=0;[].forEach.call(nav.querySelectorAll('.docs-group'),function(g){var any=false;[].forEach.call(g.querySelectorAll('.docs-nav-item'),function(it){var sec=sectionEl(it.dataset.id);var hay=(it.textContent+' '+(sec?sec.textContent:'')).toLowerCase();var show=!term||hay.indexOf(term)>=0;it.style.display=show?'':'none';if(show){any=true;total++;}});g.style.display=any?'':'none';});nofind.hidden=!term||total>0;nofind.textContent='No section mentions “'+raw+'”.';});
window.addEventListener('hashchange',function(){var id=(location.hash||'').replace(/^#/,'');if(ORDER.indexOf(id)>=0)activate(id,false);});
var initial=(location.hash||'').replace(/^#/,'');activate(ORDER.indexOf(initial)>=0?initial:ORDER[0],false);</script>
`

const newHtml = head
  + `<div class="docs-shell">\n`
  + `<nav class="docs-nav" aria-label="Documentation"><input class="docs-search" type="text" placeholder="Search docs…" aria-label="Search docs" /><p class="docs-nofind" hidden></p>${navHtml}</nav>\n`
  + `<main class="docs-content">${articles}`
  + `<div class="docs-prevnext"><button class="ghost" id="docPrev"></button><button class="ghost" id="docNext"></button></div></main>\n`
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

// GitHub's heading anchor: punctuation dropped and every space a hyphen, so "Hosting & your
// data" is #hosting--your-data. The same rule as ghSlug above, which the site reads them with.
const mdSlug = ghSlug

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

Olisar is a self-hosted AI bot for Discord. It runs as your own Discord bot, from a desktop app on your computer or on a cloud server you control, and it uses your own free Google Gemini key.

This file has the same pages as the console's Docs tab, plus the setup guide. If you're new, read [${byId.overview.title}](#${mdSlug(byId.overview.title)}), then [Setup](#setup).

> [!NOTE]
> This file is generated from [web/src/docs.tsx](web/src/docs.tsx) and the Setup chapter below. Edit those, then run \`node web/scripts/build-docs-site.mjs\` to rebuild this file and \`docs/docs.html\`. Writing rules are in [web/DOCS_STYLE.md](web/DOCS_STYLE.md).

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
