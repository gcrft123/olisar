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

// Editor theme tuned to the console palette (near-black inset ground, the doc's
// syntax hues) so the editor reads as part of the system rather than stock VS Code.
monaco.editor.defineTheme('olisar-dark', {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: '', foreground: 'ededee' },
    { token: 'comment', foreground: '6a6a73', fontStyle: 'italic' },
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
