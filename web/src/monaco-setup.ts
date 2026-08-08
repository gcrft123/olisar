// Monaco wiring for the extension editor. Kept in its own module so it's only pulled
// in when the operator opens the authoring tab (lazy). Workers and the editor core are
// bundled locally (no CDN) so it works inside the offline desktop build.
//
// Import the editor and the two languages we use, not the `monaco-editor` barrel: that
// barrel drags in every basic-language grammar Monaco ships — Cameligo, Postiats, MIPS,
// Twig, Redshift, ~80 in all. They were emitted as separate chunks so nobody downloaded
// them, but they made up roughly 10 of the built bundle's 11 MB, and the desktop app
// packages the whole directory. This editor only ever opens TypeScript.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/editor/editor.all.js'                            // find, folding, suggest, context menu…
import 'monaco-editor/esm/vs/language/typescript/monaco.contribution'         // the TS language service
import 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution'
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
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

// Editor theme tuned to the console palette (near-black inset ground, the doc's
// syntax hues) so the editor reads as part of the system rather than stock VS Code.
monaco.editor.defineTheme('olisar-dark', {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: '', foreground: 'ededee' },
    { token: 'comment', foreground: '7f7f8a', fontStyle: 'italic' },  /* --text-3; 4.4:1 on the editor ground */
    { token: 'string', foreground: '7fd1a0' },
    { token: 'keyword', foreground: 'b69cff' },
    { token: 'number', foreground: 'e0a458' },
    { token: 'type', foreground: 'e0a458' },
    { token: 'type.identifier', foreground: 'e0a458' },
    { token: 'identifier', foreground: 'ededee' },
    { token: 'delimiter', foreground: '9d9da7' },
    { token: 'operator', foreground: '9d9da7' },
  ],
  colors: {
    'editor.background': '#0f0f12',
    'editor.foreground': '#ededee',
    'editorLineNumber.foreground': '#3a3a40',
    'editorLineNumber.activeForeground': '#9d9da7',
    'editor.selectionBackground': '#26262a',
    'editor.lineHighlightBackground': '#15151a',
    'editorCursor.foreground': '#8a8af2',
    'editorIndentGuide.background1': '#1c1c20',
    'editorWidget.background': '#08080a',
    'editorWidget.border': '#26262a',
    'editorSuggestWidget.background': '#08080a',
    'editorSuggestWidget.border': '#26262a',
    'input.background': '#0f0f12',
    'dropdown.background': '#08080a',
    'scrollbarSlider.background': '#32323766',
  },
})

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
