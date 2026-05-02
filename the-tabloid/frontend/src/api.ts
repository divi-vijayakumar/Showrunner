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

// -- Short Drama -------------------------------------------------------------

export interface DramaAsset {
  asset_id: string
  label: string
  kind: 'character' | 'set'
  description: string
  filename: string
  url: string
  size_bytes: number
}

export async function uploadDramaAsset(
  file: File,
  label: string,
  kind: 'character' | 'set',
  description: string,
): Promise<DramaAsset> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('label', label)
  fd.append('kind', kind)
  fd.append('description', description)
  // Don't set Content-Type — browser sets the multipart boundary.
  const r = await fetch(`${API_BASE}/api/drama/upload`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) throw new Error(`upload failed: ${r.status} ${await r.text()}`)
  return r.json()
}

export interface DramaGenerateBody {
  beat_sheet: string
  title: string
  default_language: 'tamil' | 'english'
  characters: { asset_id: string; label: string; description: string }[]
  sets: { asset_id: string; label: string; description: string }[]
}

export async function generateDrama(
  body: DramaGenerateBody,
): Promise<{ segment_id: string }> {
  return http('/api/drama/generate', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function fetchRecentDramaAssets(
  limit: number = 50,
): Promise<{ assets: (DramaAsset & { has_label: boolean; uploaded_at: number })[] }> {
  return http(`/api/drama/recent-assets?limit=${limit}`)
}

export async function cancelDrama(
  segment_id: string,
): Promise<{ segment_id: string; cancelled: boolean; newly_cancelled: boolean }> {
  return http(`/api/drama/cancel/${segment_id}`, { method: 'POST' })
}

export async function relabelDramaAsset(
  asset_id: string,
  label: string,
  kind: 'character' | 'set',
  description: string = '',
): Promise<DramaAsset> {
  return http(`/api/drama/asset/${asset_id}/label`, {
    method: 'POST',
    body: JSON.stringify({ label, kind, description }),
  })
}
