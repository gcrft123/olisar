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
