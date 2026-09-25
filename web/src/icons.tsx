// Central icon registry — every glyph in the console comes from the Solar icon
// set (https://github.com/480-Design/Solar-Icon-Set) via @solar-icons/react.
// No emoji anywhere; semantic names map to a single source of truth here.
//
// Keep this list to names actually in use: App indexes it dynamically (`Icon[n.ic]`), so
// the bundler can't drop an entry nobody renders.

import {
  UserCircle,
  Tuning2,
  ChatRoundLine,
  Hashtag,
  ShieldKeyhole,
  PlugCircle,
  DocumentText,
  BookBookmark,
  ChartSquare,
  Logout2,
  Login3,
  CheckCircle,
  AddCircle,
  TrashBinMinimalistic,
  DangerTriangle,
  KeyMinimalistic,
  UsersGroupRounded,
  TestTube,
  Settings,
  Palette,
  Refresh,
  Pulse,
  Download,
  Routing,
  Flag2,
  ShieldWarning,
  ForbiddenCircle,
  CodeSquare,
  Bolt,
  Magnifer,
  AltArrowLeft,
  AltArrowRight,
  AltArrowDown,
  InfoCircle,
  ShieldCheck,
  Copy,
  DownloadMinimalistic,
  UploadMinimalistic,
  PenNewSquare,
  Plain2,
  Eraser,
  Star,
  HamburgerMenu,
  Eye,
  EyeClosed,
} from '@solar-icons/react'

export const Icon = {
  persona: UserCircle,
  behavior: Tuning2,
  messages: ChatRoundLine,
  channels: Hashtag,
  access: ShieldKeyhole,
  extensions: PlugCircle,
  docs: DocumentText,
  knowledge: BookBookmark,
  usage: ChartSquare,
  logout: Logout2,
  login: Login3,
  check: CheckCircle,
  add: AddCircle,
  trash: TrashBinMinimalistic,
  warn: DangerTriangle,
  keys: KeyMinimalistic,
  members: UsersGroupRounded,
  sandbox: TestTube,
  settings: Settings,
  palette: Palette,
  refresh: Refresh,
  pulse: Pulse,
  update: Download,
  remote: Routing,
  flag: Flag2,
  developer: ShieldWarning,
  ban: ForbiddenCircle,
  code: CodeSquare,
  bolt: Bolt,
  search: Magnifer,
  arrowLeft: AltArrowLeft,
  arrowRight: AltArrowRight,
  chevron: AltArrowDown,
  info: InfoCircle,
  verified: ShieldCheck,
  copy: Copy,
  download: DownloadMinimalistic,
  upload: UploadMinimalistic,
  edit: PenNewSquare,
  send: Plain2,
  eraser: Eraser,
  star: Star,
  menu: HamburgerMenu,
  eye: Eye,
  eyeOff: EyeClosed,
} as const

export type IconName = keyof typeof Icon

// The copy → copied swap, shared by every copy affordance in the console.
//
// Both glyphs stay mounted and cross-fade. The ternary this replaces unmounted the copy
// icon on the frame the check appeared, so only the arrival was ever animated — and it
// arrived on a `scale(0.4) → 1.12 → 1` keyframe, whose overshoot is the one thing an icon
// transition should never have.
export function CopyGlyph({ copied, size = 15 }: { copied: boolean; size?: number }) {
  return (
    <span className={'copyglyph' + (copied ? ' on' : '')} style={{ width: size, height: size }}>
      <Copy size={size} aria-hidden />
      <CheckCircle size={size} weight="Bold" aria-hidden />
    </span>
  )
}

// Discord's own logo, for the Discord-blue buttons that hand off to Discord (adding the bot
// to a server). A brand mark, so it's filled and drawn as Discord ships it, not a Solar glyph.
export function DiscordLogo({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.758} viewBox="0 0 127.14 96.36" fill="currentColor" aria-hidden="true">
      <path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z" />
    </svg>
  )
}

// A plain line-stroke check, for a Get started step that's done. Two strokes, so it stays at
// 2 for the same reason as CloseX below. `pathLength` makes the path 1 long, so CSS can draw
// it in with a dash offset.
export function CheckMark({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7" pathLength={1} />
    </svg>
  )
}

// A plain line-stroke "×" for modal/menu close affordances — lighter than the
// circled Solar CloseCircle, which reads too heavy at small sizes. Deliberately 2 where
// the Solar Linear set is 1.5: a two-stroke × carries a fraction of a full glyph's ink,
// so matching the number would make it optically lighter, not equal.
export function CloseX({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}
