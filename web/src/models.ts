/** "gemini-3.1-flash-lite" → "Gemini 3.1 Flash-Lite", "gemini-flash-latest" → "Gemini Flash
 *  (latest)". A model it doesn't recognise keeps its id. */
export function modelName(id: string) {
  const m = id.match(/^gemini-(?:(\d[\d.]*)-)?(flash-lite|flash|pro)(?:-(latest|preview))?$/)
  if (!m) return id
  const [, version, family, tag] = m
  const fam = family === 'flash-lite' ? 'Flash-Lite' : family[0].toUpperCase() + family.slice(1)
  return `Gemini ${version ? version + ' ' : ''}${fam}${tag === 'latest' ? ' (latest)' : ''}`
}
