// Build a print-ready PDF of the in-dashboard documentation.
//
// Reads the same `DOCS` / `DOC_GROUPS` content the console renders (src/docs.tsx),
// turns its Markdown (callouts, tables, headings, links) into a clean light-themed
// HTML doc, and prints it to PDF with headless Chrome. No new dependencies — it
// reuses the project's esbuild to load the .tsx data module.
//
//   node web/scripts/build-docs-pdf.mjs            # -> Olisar-Documentation.pdf (repo root)
//   node web/scripts/build-docs-pdf.mjs out.pdf

import { transformSync } from 'esbuild'
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webDir = resolve(here, '..')
const repoRoot = resolve(webDir, '..')
const outPdf = resolve(repoRoot, process.argv[2] || 'Olisar-Documentation.pdf')

// ── Load the docs data (transpile the .tsx data module, then import it) ───────
const tsx = readFileSync(resolve(webDir, 'src/docs.tsx'), 'utf8')
const js = transformSync(tsx, { loader: 'tsx', format: 'esm' }).code
const tmpModule = resolve('/tmp', '_olisar_docs.mjs')
writeFileSync(tmpModule, js)
const { DOCS, DOC_GROUPS } = await import(pathToFileURL(tmpModule).href)

// ── Markdown -> HTML (mirrors the app's renderer: callouts, tables, etc.) ─────
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

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
      // In-app links (#section / tab:id) have no target in a standalone PDF — render
      // them as styled references rather than dead links.
      if (url.startsWith('#') || url.startsWith('tab:')) out += `<span class="ref">${label}</span>`
      else out += `<a href="${esc(url)}">${label}</a>`
    }
    last = m.index + t.length
  }
  if (last < text.length) out += esc(text.slice(last))
  return out
}

const CALLOUT_LABELS = { tip: 'Tip', note: 'Note', warning: 'Warning', info: 'Info' }
const splitRow = (l) => l.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
const isTableSep = (l) => /^\|?[\s:|-]+\|?$/.test(l.trim()) && l.includes('-') && l.includes('|')

function renderBlocks(lines) {
  let out = ''
  let list = []
  let para = []
  const flushList = () => { if (list.length) { out += '<ul>' + list.map((li) => `<li>${inline(li)}</li>`).join('') + '</ul>'; list = [] } }
  const flushPara = () => { if (para.length) { out += `<p>${inline(para.join(' '))}</p>`; para = [] } }

  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    const cm = line.match(/^:::(tip|note|warning|info)\s*(.*)$/)
    if (cm) {
      flushList(); flushPara()
      const inner = []
      i++
      while (i < lines.length && lines[i].trim() !== ':::') { inner.push(lines[i]); i++ }
      i++
      out += `<div class="callout ${cm[1]}"><div class="callout-label">${esc(cm[2].trim() || CALLOUT_LABELS[cm[1]])}</div><div class="callout-body">${renderBlocks(inner)}</div></div>`
      continue
    }
    if (line.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushList(); flushPara()
      const header = splitRow(line)
      i += 2
      const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(splitRow(lines[i].trim())); i++ }
      out += '<table><thead><tr>' + header.map((h) => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>'
        + rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>'
      continue
    }
    if (!line) { flushList(); flushPara(); i++; continue }
    if (line.startsWith('### ')) { flushList(); flushPara(); out += `<h3>${inline(line.slice(4))}</h3>`; i++; continue }
    if (line.startsWith('## ')) { flushList(); flushPara(); out += `<h2>${inline(line.slice(3))}</h2>`; i++; continue }
    if (line.startsWith('- ')) { flushPara(); list.push(line.slice(2)); i++; continue }
    if (list.length) { list[list.length - 1] += ' ' + line; i++; continue }
    para.push(line); i++
  }
  flushList(); flushPara()
  return out
}

// ── Assemble the document (cover + grouped TOC + a section per docs page) ─────
const byId = Object.fromEntries(DOCS.map((s) => [s.id, s]))
const ordered = DOC_GROUPS.flatMap((g) => g.ids.map((id) => byId[id]).filter(Boolean))
const today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })

const toc = DOC_GROUPS.map((g) => {
  const items = g.ids.map((id) => byId[id]).filter(Boolean)
  if (!items.length) return ''
  return `<div class="group">${esc(g.label)}</div>` + items.map((s) => `<div class="toc-item">${esc(s.title)}</div>`).join('')
}).join('')

const sections = ordered.map((s) =>
  `<section><h1>${esc(s.title)}</h1>${renderBlocks(s.body.trim().split('\n'))}</section>`
).join('')

const html = `<!doctype html><html><head><meta charset="utf-8"><title>Olisar Documentation</title><style>
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #23232a; font-size: 10.5pt; line-height: 1.55; margin: 0; }
code { font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; background: #f3f3f8; border: 1px solid #e6e6ef; border-radius: 4px; padding: 0.5px 4px; font-size: 9pt; }
strong { font-weight: 650; }
a { color: #5b5bd6; text-decoration: none; border-bottom: 1px solid #c7c7f0; }
.ref { color: #5b5bd6; font-weight: 600; }
.cover { height: 235mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
.cover .mark { width: 54px; height: 54px; border-radius: 14px; background: linear-gradient(135deg,#8a8af2,#5b5bd6); margin-bottom: 22px; }
.cover h1 { font-size: 40pt; margin: 0; color: #1a1a22; letter-spacing: -0.02em; border: 0; }
.cover .sub { font-size: 14pt; color: #6a6a78; margin-top: 10px; }
.cover .meta { margin-top: 30px; color: #9a9aa6; font-size: 9.5pt; }
.toc { page-break-after: always; }
.toc h2 { color: #5b5bd6; border: 0; margin: 0 0 10px; }
.toc .group { font-weight: 700; margin: 16px 0 5px; color: #2a2a33; }
.toc .toc-item { padding: 2px 0 2px 16px; color: #44444e; }
section { page-break-before: always; }
h1 { font-size: 22pt; color: #1a1a22; border-bottom: 2px solid #ececf5; padding-bottom: 7px; margin: 0 0 14px; letter-spacing: -0.01em; }
h2 { font-size: 13.5pt; color: #2a2a33; margin: 20px 0 8px; }
h3 { font-size: 11.5pt; color: #2a2a33; margin: 15px 0 6px; }
p { margin: 8px 0; }
ul { margin: 8px 0; padding-left: 20px; }
li { margin: 3px 0; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 9.5pt; break-inside: avoid; }
th, td { border: 1px solid #e2e2ec; padding: 6px 9px; text-align: left; vertical-align: top; }
th { background: #f5f5fb; font-weight: 650; }
.callout { border-left: 3px solid #b9b9e8; background: #f7f7fc; border-radius: 6px; padding: 9px 14px; margin: 12px 0; break-inside: avoid; }
.callout-label { font-weight: 700; font-size: 8pt; letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 4px; color: #5b5bd6; }
.callout-body p:first-child { margin-top: 0; } .callout-body p:last-child { margin-bottom: 0; }
.callout.tip { border-color: #2faf6a; background: #f1faf4; } .callout.tip .callout-label { color: #2a9d62; }
.callout.warning { border-color: #d9920a; background: #fdf7ec; } .callout.warning .callout-label { color: #b8800c; }
.callout.note, .callout.info { border-color: #5b5bd6; background: #f4f4fc; }
</style></head><body>
<div class="cover"><div class="mark"></div><h1>Olisar</h1><div class="sub">Console documentation</div><div class="meta">An AI companion for your Discord server · Generated ${esc(today)}</div></div>
<div class="toc"><h2>Contents</h2>${toc}</div>
${sections}
</body></html>`

const htmlPath = resolve('/tmp', '_olisar_docs.html')
writeFileSync(htmlPath, html)

// ── Print to PDF with headless Chrome ─────────────────────────────────────────
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
execFileSync(chrome, [
  '--headless=new',
  '--disable-gpu',
  '--no-pdf-header-footer',
  `--print-to-pdf=${outPdf}`,
  pathToFileURL(htmlPath).href,
], { stdio: 'ignore' })

if (!existsSync(outPdf)) { console.error('PDF was not produced'); process.exit(1) }
console.log(`✓ ${ordered.length} sections -> ${outPdf} (${(statSync(outPdf).size / 1024).toFixed(0)} kB)`)
