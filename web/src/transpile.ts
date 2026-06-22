// In-browser TypeScript -> JS transpile via esbuild-wasm. The emitted JS is what the
// backend stores and runs in the sandbox. The wasm is bundled locally (?url) so it
// works offline in the desktop build.
import * as esbuild from 'esbuild-wasm'
import wasmURL from 'esbuild-wasm/esbuild.wasm?url'

let ready: Promise<void> | null = null
function init(): Promise<void> {
  // worker:false keeps it simple/robust in the offline desktop bundle; extensions are
  // tiny so transpiling on the main thread is instant.
  if (!ready) ready = esbuild.initialize({ wasmURL, worker: false })
  return ready
}

export async function transpile(source: string): Promise<string> {
  await init()
  const out = await esbuild.transform(source, {
    loader: 'ts',
    target: 'es2020',
    // QuickJS has no module system; keep it as a plain top-level script.
    format: 'iife',
  })
  return out.code
}
