#!/usr/bin/env node
/*
 * Design-system linter for the Olisar console — enforces web/DESIGN.md rules at build time.
 * Runs before tsc/vite in `npm run build`; exits non-zero (fails the build) on any violation.
 * No dependencies. Rules:
 *   - no-raw-color : raw hex / rgb() / hsl() outside the :root token block or an inline style
 *                    (use a var(--…) token). Sanctioned brand/syntax/neutral colours allowlisted.
 *   - btn-sm       : a TEXT button uses `sm` (only icon-btn may be `sm`; text buttons are 34px).
 *   - btn-variant  : a button mixes >1 colour variant (e.g. `ghost danger`).
 *   - btn-warn     : a button uses the class `warn` (the amber button variant is `caution`).
 *   - native-dialog: window.alert/confirm/prompt (use the overlays toast()/confirmDialog()).
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, extname, basename, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

// Sanctioned raw colours (lowercased): Discord brand, code-syntax token hues, pure black/white.
const COLOR_ALLOW = new Set([
  '#5865f2', '#4752e0',            // Discord brand (the one off-palette exception)
  '#7fd1a0', '#b69cff', '#e0a458', // code-preview syntax tokens (DESIGN.md)
  '#fff', '#ffffff', '#000', '#000000',
])
// Files where raw hex is unavoidable (Monaco theme, the accent-token source).
const COLOR_EXEMPT = new Set(['monaco-setup.ts', 'theme.ts'])

// The established spacing rhythm (px). padding / margin / gap must use these values;
// corner radii must use var(--radius*) tokens (raw 0, 50%, ≤6px chips excepted); and
// custom easing must be var(--ease-out). These prevent arbitrary drift.
const SPACING = new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 44, 48, 56, 64, 72, 80, 96, 128])

const BTN_VARIANTS = new Set(['primary', 'ghost', 'danger', 'caution'])

const violations = []
const add = (file, line, rule, msg) => violations.push({ file: relative(SRC, file), line, rule, msg })
const lineOf = (src, idx) => src.slice(0, idx).split('\n').length

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    out.push(...(statSync(p).isDirectory() ? walk(p) : [p]))
  }
  return out
}

// Raw colour literals on a single text fragment (hex + rgb/hsl, minus allowlist + black shadows).
function rawColors(text) {
  const hits = []
  for (const h of text.match(/#[0-9a-fA-F]{3,8}\b/g) || []) {
    if (!COLOR_ALLOW.has(h.toLowerCase())) hits.push(h)
  }
  for (const fn of text.match(/\b(?:rgb|rgba|hsl|hsla)\([^)]*\)/g) || []) {
    if (!/\b(?:rgba?)\(\s*0\s*,\s*0\s*,\s*0\b/.test(fn)) hits.push(fn) // allow rgba(0,0,0,*) shadows
  }
  return hits
}

// Parse <button …> opening tags, brace/quote-aware (handlers contain `=>`).
function buttonTags(src) {
  const tags = []
  let i = 0
  while ((i = src.indexOf('<button', i)) !== -1) {
    let j = i + 7, depth = 0, quote = null
    for (; j < src.length; j++) {
      const c = src[j]
      if (quote) { if (c === quote && src[j - 1] !== '\\') quote = null }
      else if (c === '"' || c === "'" || c === '`') quote = c
      else if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) break
    }
    tags.push({ text: src.slice(i, j + 1), line: lineOf(src, i) })
    i = j + 1
  }
  return tags
}

function checkButtonClasses(cls, file, line) {
  const tokens = cls.split(/\s+/).filter(Boolean)
  if (tokens.includes('warn')) add(file, line, 'btn-warn', `button class "warn" — the amber variant is "caution"`)
  const variants = tokens.filter((t) => BTN_VARIANTS.has(t))
  if (variants.length > 1) add(file, line, 'btn-variant', `button mixes variants "${variants.join(' ')}" — use exactly one`)
  if (tokens.includes('sm') && !tokens.includes('icon-btn')) {
    add(file, line, 'btn-sm', `text button uses "sm" — only icon-btn may be sm (text buttons are 34px)`)
  }
}

for (const file of walk(SRC)) {
  const ext = extname(file)
  const name = basename(file)
  if (ext !== '.tsx' && ext !== '.ts' && ext !== '.css') continue
  const src = readFileSync(file, 'utf8')

  // ── CSS: raw colours outside :root ─────────────────────────────────────────
  if (ext === '.css') {
    let inRoot = false, depth = 0
    src.split('\n').forEach((l, i) => {
      const ln = i + 1
      if (!inRoot && /:root\b[^{]*\{/.test(l)) { inRoot = true; depth = 1; return }
      if (inRoot) { depth += (l.match(/\{/g) || []).length - (l.match(/\}/g) || []).length; if (depth <= 0) inRoot = false; return }
      for (const c of rawColors(l)) add(file, ln, 'no-raw-color', `raw colour ${c} — use a var(--…) token`)
      if (/cubic-bezier\(/.test(l)) add(file, ln, 'ease-token', `raw cubic-bezier() — use var(--ease-out)`)
      // corner radii → var(--radius*) (only 0 / 50% / ≤6px chips allowed raw)
      for (const mm of l.matchAll(/[a-z-]*radius:\s*([^;{]+)/g)) {
        for (const part of mm[1].trim().split(/\s+/)) {
          if (!/px$/.test(part)) continue
          if (+part.slice(0, -2) <= 6) continue
          add(file, ln, 'radius-token', `border-radius "${part}" — use a var(--radius*) token`)
        }
      }
      // padding / margin / gap → established spacing scale
      for (const mm of l.matchAll(/\b(?:row-gap|column-gap|gap|padding|margin)(?:-(?:top|right|bottom|left))?:\s*([^;{]+)/g)) {
        for (const px of mm[1].matchAll(/\b(\d+)px\b/g)) {
          if (!SPACING.has(+px[1])) add(file, ln, 'spacing-scale', `spacing ${px[1]}px is off the established scale`)
        }
      }
    })
    continue
  }

  // ── TSX/TS ─────────────────────────────────────────────────────────────────
  // Native dialogs (confirmDialog/promptDialog are fine — they have `Dialog` after).
  let m
  const dlgRe = /\b(?:window\.)?(alert|confirm|prompt)\s*\(/g
  while ((m = dlgRe.exec(src))) add(file, lineOf(src, m.index), 'native-dialog', `${m[1]}() — use overlays toast()/confirmDialog()/promptDialog()`)

  if (ext === '.tsx') {
    // Buttons. Static className → check the whole string. Dynamic className={…} →
    // check each string literal independently (ternary branches are alternatives,
    // not a combined class list — joining them would false-positive on a+b vs a?b:c).
    for (const tag of buttonTags(src)) {
      const stat = tag.text.match(/className="([^"]*)"/)
      if (stat) { checkButtonClasses(stat[1], file, tag.line); continue }
      const dyn = tag.text.match(/className=\{([\s\S]*?)\}/)
      if (dyn) {
        for (const s of dyn[1].match(/['"`]([^'"`]*)['"`]/g) || []) checkButtonClasses(s.slice(1, -1), file, tag.line)
      }
    }
    // Inline style={{ … }} — raw colours, off-scale spacing, raw radii
    if (!COLOR_EXEMPT.has(name)) {
      const styleRe = /style=\{\{([^}]*)\}\}/g
      while ((m = styleRe.exec(src))) {
        const body = m[1], ln = lineOf(src, m.index)
        for (const c of rawColors(body)) add(file, ln, 'no-raw-color', `inline style raw colour ${c} — use var(--…)`)
        for (const sp of body.matchAll(/\b(?:gap|rowGap|columnGap|padding(?:Top|Right|Bottom|Left)?|margin(?:Top|Right|Bottom|Left)?):\s*(\d+)\b/g)) {
          if (!SPACING.has(+sp[1])) add(file, ln, 'spacing-scale', `inline spacing ${sp[1]} is off the established scale`)
        }
        for (const rr of body.matchAll(/borderRadius:\s*(\d+)\b/g)) {
          if (+rr[1] > 6) add(file, ln, 'radius-token', `inline borderRadius ${rr[1]} — use var(--radius*)`)
        }
      }
    }
  }
}

if (violations.length) {
  violations.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line)
  console.error(`\n✖ design-lint: ${violations.length} violation(s) of web/DESIGN.md\n`)
  for (const v of violations) console.error(`  ${v.file}:${v.line}  [${v.rule}]  ${v.msg}`)
  console.error('')
  process.exit(1)
}
console.log('✓ design-lint: all DESIGN.md checks passed')
