import type {
  ChannelId,
  ChannelInfo,
  DebateMessage,
  Persona,
  Segment,
  SegmentMode,
  StoryCandidate,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '')

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText} — ${path}`)
  }
  return r.json() as Promise<T>
}

export async function fetchChannels(): Promise<Record<ChannelId, ChannelInfo>> {
  return http('/api/channels')
}

export async function startSegment(
  channel: ChannelId,
  personas?: Persona[],
  mode: SegmentMode = 'tabloid',
  story?: StoryCandidate | null,
): Promise<{ segment_id: string }> {
  return http(`/api/generate/${channel}`, {
    method: 'POST',
    body: JSON.stringify({
      personas: personas ?? null,
      mode,
      story: story ?? null,
    }),
  })
}

export async function fetchChannelStories(
  channel: ChannelId,
  limit: number = 8,
): Promise<{ channel: ChannelId; stories: StoryCandidate[] }> {
  return http(`/api/channels/${channel}/stories?limit=${limit}`)
}

export async function fetchSegment(
  segmentId: string,
): Promise<{ segment: Segment; messages: DebateMessage[] }> {
  return http(`/api/segment/${segmentId}`)
}
