// What the bot is called in Discord. Operators bring their own Discord bot and name it
// themselves, so the console says that name wherever it means the bot, and "Olisar" only
// where it means the app. Set from /api/me (or the member session) before any page that
// says it renders, the same way api.ts holds the current server.

let current = ''

export function setBotName(name: string | null | undefined): void {
  current = (name || '').trim()
}

// "Olisar" until the backend has said otherwise: before the bot has ever logged in, the
// console has no other name to give it.
export function botName(): string {
  return current || 'Olisar'
}
