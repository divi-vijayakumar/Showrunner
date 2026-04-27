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

export async function fetchSegmentList(
  limit: number = 20,
  channel?: ChannelId,
): Promise<{ segments: (Segment & { id: string })[] }> {
  const q = new URLSearchParams({ limit: String(limit) })
  if (channel) q.set('channel', channel)
  return http(`/api/segments?${q}`)
}

export async function fetchSegment(
  segmentId: string,
): Promise<{ segment: Segment; messages: DebateMessage[] }> {
  return http(`/api/segment/${segmentId}`)
}

export async function startDirect(
  scriptName: string,
): Promise<{ segment_id: string }> {
  return http(`/api/generate-direct/${scriptName}`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export interface CastForStoryResult {
  channel: ChannelId
  personas: Persona[]
  anchor: Persona | null
  guests: Persona[]
  // null when casting succeeded; otherwise one of:
  //   "no_anchor" | "casting_error" | "casting_underdelivered"
  fallback: string | null
  error?: string
  // The structured brief select_specific_story produced. Pass it back
  // to /api/generate as `picked_story` so the pipeline doesn't recompute.
  story_brief?: Record<string, unknown> | null
}

export async function castForStory(
  channel: ChannelId,
  story: StoryCandidate,
): Promise<CastForStoryResult> {
  return http(`/api/cast-for-story/${channel}`, {
    method: 'POST',
    body: JSON.stringify({ story }),
  })
}

export const apiBase = API_BASE
