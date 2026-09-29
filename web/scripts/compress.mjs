#!/usr/bin/env node
/*
 * Writes a Brotli (.br) and a gzip (.gz) copy beside every text file in dist/, so the backend
 * (olisar/runtime/console_files.py) can send a browser that accepts them the compressed copy
 * as it is, instead of compressing on every start. Runs after `vite build` in `npm run build`.
 * The console's main script is about 1.1 MB as built and 300 KB compressed; the extension
 * editor's TypeScript worker alone is 6 MB. No dependencies: node:zlib does both.
 *
 * The file types and the size floor match COMPRESSIBLE and MIN_COMPRESS_BYTES there. A copy
 * that comes out no smaller than its file isn't written.
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, extname, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { brotliCompressSync, gzipSync, constants } from 'node:zlib'

const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const TYPES = new Set(['.js', '.mjs', '.css', '.html', '.svg', '.json', '.map', '.txt', '.ttf'])
const MIN_BYTES = 1024

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) yield* files(path)
    else yield path
  }
}

let before = 0
let after = 0
for (const path of files(DIST)) {
  if (!TYPES.has(extname(path).toLowerCase())) continue
  const data = readFileSync(path)
  if (data.length < MIN_BYTES) continue
  const br = brotliCompressSync(data, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
      [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
    },
  })
  const gz = gzipSync(data, { level: 9 })
  if (br.length < data.length) writeFileSync(path + '.br', br)
  if (gz.length < data.length) writeFileSync(path + '.gz', gz)
  before += data.length
  after += Math.min(br.length, data.length)
}
const kb = (n) => `${Math.round(n / 1024)} KB`
console.log(`compress: ${kb(before)} of text in ${DIST} is ${kb(after)} as Brotli`)
