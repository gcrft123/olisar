// Opening the Feedback form from wherever someone gets stuck.
//
// The form lives in the Settings modal, and it used to be reachable only by knowing it was
// there: Settings → Feedback, two clicks behind a gear. The places people actually get
// stuck — a deploy that failed, a page that crashed, an account that was suspended — had no
// way to it, and none of them could hand it what had just gone wrong.
//
// Whichever screen owns a Settings modal registers itself as the host, and `openFeedback`
// asks the newest one to open Feedback with a pre-fill. A screen with no modal of its own
// gets `false` back, which is FeedbackButton's cue to open one. No React in here, so
// ui.tsx and pages.tsx can import it without a cycle through settings.tsx.

export type FeedbackPrefill = {
  category?: 'Feedback' | 'Bug report' | 'Question'
  /** Written into the message box: the facts the screen already has, and a prompt for the
   *  part only the reporter knows. Closing it untouched doesn't count as discarding work. */
  message?: string
  /** Start with "Add bot logs" turned on. The reporter can still turn it off. */
  logs?: boolean
  /** Don't offer bot logs at all: the sender isn't this install's operator (a refused sign-in),
   *  and the server drops them for that sender anyway. */
  noLogs?: boolean
}

type Host = (prefill?: FeedbackPrefill) => void
const hosts: Host[] = []

/** Become the place Feedback opens. Returns the unregister function. */
export function registerFeedbackHost(host: Host): () => void {
  hosts.push(host)
  return () => {
    const i = hosts.lastIndexOf(host)
    if (i >= 0) hosts.splice(i, 1)
  }
}

export function hasFeedbackHost(): boolean {
  return hosts.length > 0
}

/** Open Feedback in the current screen's Settings modal. False when nothing is listening. */
export function openFeedback(prefill?: FeedbackPrefill): boolean {
  const host = hosts[hosts.length - 1]
  if (!host) return false
  host(prefill)
  return true
}

/** The last `lines` of a log, which is where a failure explains itself. */
export function logTail(text: string, lines = 40): string {
  const all = (text || '').trimEnd().split('\n')
  return all.length > lines ? ['…', ...all.slice(-lines)].join('\n') : all.join('\n')
}

/** A report's body: what happened, the error as the screen showed it, and the prompt for
 *  the reporter's half. Same shape as the blank-reply report in settings.tsx. */
export function reportBody(what: string, error?: string, ask = 'What I was doing:'): string {
  return [what, ...(error ? ['', 'Error:', error] : []), '', ask, ''].join('\n')
}
