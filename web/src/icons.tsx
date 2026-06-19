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
} as const

export type IconName = keyof typeof Icon
