// Monaco wiring for the extension editor. Kept in its own module so it's only pulled
// in when the operator opens the authoring tab (lazy). Workers and the editor core are
// bundled locally (no CDN) so it works inside the offline desktop build.
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'
import { loader } from '@monaco-editor/react'

;(self as any).MonacoEnvironment = {
  getWorker(_: unknown, label: string) {
    if (label === 'typescript' || label === 'javascript') return new tsWorker()
    return new editorWorker()
  },
}

loader.config({ monaco })

monaco.languages.typescript.typescriptDefaults.setCompilerOptions({
  target: monaco.languages.typescript.ScriptTarget.ES2020,
  lib: ['es2020'],
  allowNonTsExtensions: true,
  moduleResolution: monaco.languages.typescript.ModuleResolutionKind.NodeJs,
  noEmit: true,
})

let dtsAdded = false
export function ensureSdkTypes(dts: string): void {
  if (dtsAdded || !dts) return
  monaco.languages.typescript.typescriptDefaults.addExtraLib(dts, 'ts:olisar-sdk.d.ts')
  dtsAdded = true
}

export { monaco }
