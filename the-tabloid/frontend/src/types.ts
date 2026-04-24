export type AgentRole = 'anchor' | 'provocateur' | 'analyst' | 'humanist'

export type ChannelId =
  | 'india_politics'
  | 'technology'
  | 'ai_updates'
  | 'celebrity'
  | 'geopolitics'
  | 'fashion'

export type SegmentStatus = 'queued' | 'debate' | 'generating' | 'ready' | 'failed'

export interface PersonaVoice {
  gender?: string
  pace?: number
  warmth?: string
}

export interface Persona {
  id: string
  name: string
  role: AgentRole
  lean: string
  culture?: string
  style?: string
  voice?: PersonaVoice
}

export interface ChannelInfo {
  id: ChannelId
  label: string
  icon: string
  color: string
  default_panel: Persona[]
  alternatives: Persona[]
}

export interface Segment {
  channel: ChannelId
  headline?: string
  source?: string
  status: SegmentStatus
  progress: number
  video_url?: string
  personas?: Persona[]
  infographics?: unknown[]
  error?: string
  created_at?: number
}

export interface DebateMessage {
  agent: AgentRole
  persona_name: string
  persona_lean: string
  content: string
  seq: number
  created_at?: number
}
