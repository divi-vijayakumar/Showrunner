import { useEffect, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { CHANNEL_UI } from '../config/channelUi'
import { fetchSegmentList } from '../api'
import type { ChannelId, Segment } from '../types'

type Row = Segment & { id: string }

const CHANNEL_LABEL: Record<ChannelId, string> = {
  india_politics: 'India Politics',
  technology: 'Technology',
  ai_updates: 'AI Updates',
  celebrity: 'Celebrity',
  geopolitics: 'Geopolitics',
  fashion: 'Fashion',
  investments_financials: 'Investments & Financials',
}

function timeAgo(secs?: number) {
  if (!secs) return ''
  const delta = Math.max(0, Math.floor(Date.now() / 1000 - secs))
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return `${Math.floor(delta / 86400)}d ago`
}

export function History({
  onBack,
  onOpen,
}: {
  onBack: () => void
  onOpen: (seg: Row) => void
}) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchSegmentList(30)
      .then((d) => !cancelled && setRows(d.segments))
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="min-h-screen relative pb-28">
      <AmbientOrbs />
      <TopAppBar onBack={onBack} status="on_air" centerTitle />

      <main className="max-w-xl mx-auto px-6 pt-24 pb-12">
        <div className="mb-6 space-y-2">
          <h2 className="font-mono text-on-surface-variant uppercase tracking-[0.2em] text-[11px] font-bold">
            Recent episodes
          </h2>
          <div className="h-px w-12 bg-gradient-to-r from-fuchsia-500 to-transparent" />
        </div>

        {error && (
          <div className="glass-card rounded-xl p-4 mb-6 text-sm text-red-300">
            Couldn't load history: {error}
          </div>
        )}

        {!rows && !error && (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((k) => (
              <div key={k} className="glass-card rounded-2xl p-5 animate-pulse h-20 opacity-50" />
            ))}
          </div>
        )}

        {rows && rows.length === 0 && (
          <div className="glass-card rounded-xl p-4 text-sm text-on-surface-variant">
            No episodes yet. Start one from a channel.
          </div>
        )}

        <div className="space-y-3">
          {rows?.map((r) => {
            const ui = CHANNEL_UI[r.channel as ChannelId]
            const label = CHANNEL_LABEL[r.channel as ChannelId] ?? r.channel
            const ready = r.status === 'ready'
            const failed = r.status === 'failed'
            const inflight = !ready && !failed
            return (
              <button
                key={r.id}
                type="button"
                disabled={!ready}
                onClick={() => ready && onOpen(r)}
                className={`glass-card w-full text-left p-4 rounded-2xl flex items-center gap-4 transition-all ${
                  ready ? 'active:scale-[0.99] hover:border-white/30 cursor-pointer' : 'opacity-60 cursor-default'
                }`}
              >
                <div
                  className={`w-11 h-11 rounded-xl ${ui?.tint ?? 'bg-white/5'} ${ui?.border ?? 'border-white/10'} border flex items-center justify-center flex-shrink-0`}
                >
                  <MSym
                    name={r.media_kind === 'audio' || r.mode === 'podcast' ? 'headphones' : 'play_circle'}
                    filled
                    className={`!text-[18px] ${ui?.fg ?? 'text-white'}`}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-on-surface-variant/70 truncate">
                      {label}
                    </span>
                    <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/5 text-on-surface-variant/60">
                      {r.mode === 'podcast' ? 'listen' : 'watch'}
                    </span>
                    {failed && (
                      <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">
                        failed
                      </span>
                    )}
                    {inflight && (
                      <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-fuchsia-500/20 text-fuchsia-300">
                        {r.status} · {r.progress ?? 0}%
                      </span>
                    )}
                  </div>
                  <div className="font-body text-[13px] text-white truncate">
                    {r.headline ?? 'Untitled segment'}
                  </div>
                  <div className="font-mono text-[10px] text-on-surface-variant/60 mt-0.5">
                    {r.source ?? ''} · {timeAgo(r.created_at as number | undefined)}
                  </div>
                </div>
                {ready && <MSym name="chevron_right" className="text-on-surface-variant/40 flex-shrink-0" />}
              </button>
            )
          })}
        </div>
      </main>
    </div>
  )
}
