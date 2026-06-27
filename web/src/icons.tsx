// Central icon registry — every glyph in the console comes from the Solar icon
// set (https://github.com/480-Design/Solar-Icon-Set) via @solar-icons/react.
// No emoji anywhere; semantic names map to a single source of truth here.

import {
  Planet,
  UserCircle,
  Tuning2,
  ChatRoundLine,
  MagicStick3,
  Hashtag,
  ShieldKeyhole,
  PlugCircle,
  DocumentText,
  BookBookmark,
  Notebook,
  ChartSquare,
  Logout2,
  Login3,
  CheckCircle,
  AddCircle,
  TrashBinMinimalistic,
  DangerTriangle,
  KeyMinimalistic,
  UsersGroupRounded,
  Power,
  TestTube,
  Settings,
  Palette,
  Refresh,
  Download,
  Routing,
  Flag2,
  ShieldWarning,
  ForbiddenCircle,
  CodeSquare,
  Bolt,
  CloseCircle,
  Magnifer,
  AltArrowLeft,
  AltArrowRight,
  AltArrowDown,
  AltArrowUp,
  InfoCircle,
  ShieldCheck,
  Copy,
} from '@solar-icons/react'

export const Icon = {
  brand: Planet,
  persona: UserCircle,
  behavior: Tuning2,
  messages: ChatRoundLine,
  proactivity: MagicStick3,
  channels: Hashtag,
  access: ShieldKeyhole,
  extensions: PlugCircle,
  docs: DocumentText,
  knowledge: BookBookmark,
  glossary: Notebook,
  usage: ChartSquare,
  logout: Logout2,
  login: Login3,
  check: CheckCircle,
  add: AddCircle,
  trash: TrashBinMinimalistic,
  warn: DangerTriangle,
  keys: KeyMinimalistic,
  members: UsersGroupRounded,
  power: Power,
  sandbox: TestTube,
  settings: Settings,
  palette: Palette,
  refresh: Refresh,
  update: Download,
  remote: Routing,
  flag: Flag2,
  developer: ShieldWarning,
  ban: ForbiddenCircle,
  code: CodeSquare,
  bolt: Bolt,
  close: CloseCircle,
  search: Magnifer,
  arrowLeft: AltArrowLeft,
  arrowRight: AltArrowRight,
  chevron: AltArrowDown,
  arrowUp: AltArrowUp,
  info: InfoCircle,
  verified: ShieldCheck,
  copy: Copy,
} as const

export type IconName = keyof typeof Icon

// A plain line-stroke "×" for modal/menu close affordances — lighter than the
// circled Solar CloseCircle, which reads too heavy at small sizes.
export function CloseX({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}
