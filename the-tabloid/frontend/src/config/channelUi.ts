import type { AgentRole, ChannelId } from '../types'

/** Visual treatment for each channel tile — icon name + Tailwind accent classes. */
export const CHANNEL_UI: Record<
  ChannelId,
  {
    icon: string
    iconFilled?: boolean
    tint: string // bg tint e.g. "bg-indigo-500/20"
    border: string // border e.g. "border-indigo-500/30"
    fg: string // icon color e.g. "text-indigo-400"
    accentGradient: string // gradient from-to for display accents
  }
> = {
  india_politics: {
    icon: 'account_balance',
    tint: 'bg-indigo-500/20',
    border: 'border-indigo-500/30',
    fg: 'text-indigo-400',
    accentGradient: 'from-indigo-500 to-fuchsia-500',
  },
  technology: {
    icon: 'memory',
    tint: 'bg-purple-500/20',
    border: 'border-purple-500/30',
    fg: 'text-purple-400',
    accentGradient: 'from-purple-500 to-pink-500',
  },
  ai_updates: {
    icon: 'neurology',
    iconFilled: true,
    tint: 'bg-cyan-500/20',
    border: 'border-cyan-500/30',
    fg: 'text-cyan-400',
    accentGradient: 'from-cyan-400 to-indigo-500',
  },
  celebrity: {
    icon: 'star',
    iconFilled: true,
    tint: 'bg-red-500/20',
    border: 'border-red-500/30',
    fg: 'text-red-400',
    accentGradient: 'from-red-500 to-orange-500',
  },
  geopolitics: {
    icon: 'public',
    tint: 'bg-amber-500/20',
    border: 'border-amber-500/30',
    fg: 'text-amber-400',
    accentGradient: 'from-amber-400 to-orange-500',
  },
  fashion: {
    icon: 'checkroom',
    tint: 'bg-pink-500/20',
    border: 'border-pink-500/30',
    fg: 'text-pink-400',
    accentGradient: 'from-pink-500 to-fuchsia-500',
  },
}

export const CHANNEL_ORDER: ChannelId[] = [
  'india_politics',
  'technology',
  'ai_updates',
  'celebrity',
  'geopolitics',
  'fashion',
]

/** Persona role → Tailwind classes for badges and accents. */
export const ROLE_UI: Record<
  AgentRole,
  { label: string; bg: string; border: string; glow: string; topBorder: string; text: string }
> = {
  anchor: {
    label: 'ANCHOR',
    bg: 'bg-indigo-500',
    border: 'border-indigo-500',
    glow: 'shadow-indigo-500/40',
    topBorder: 'border-t-indigo-500',
    text: 'text-indigo-300',
  },
  provocateur: {
    label: 'PROVOCATEUR',
    bg: 'bg-red-500',
    border: 'border-red-500',
    glow: 'shadow-red-500/40',
    topBorder: 'border-t-red-500',
    text: 'text-red-300',
  },
  analyst: {
    label: 'ANALYST',
    bg: 'bg-emerald-500',
    border: 'border-emerald-500',
    glow: 'shadow-emerald-500/40',
    topBorder: 'border-t-emerald-500',
    text: 'text-emerald-300',
  },
  humanist: {
    label: 'HUMANIST',
    bg: 'bg-amber-500',
    border: 'border-amber-500',
    glow: 'shadow-amber-500/40',
    topBorder: 'border-t-amber-500',
    text: 'text-amber-300',
  },
}
