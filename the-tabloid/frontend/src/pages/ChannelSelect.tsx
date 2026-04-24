import { useEffect, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { BottomNav } from '../components/BottomNav'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { CHANNEL_ORDER, CHANNEL_UI } from '../config/channelUi'
import { fetchChannels } from '../api'
import type { ChannelId, ChannelInfo } from '../types'

export function ChannelSelect({ onPick }: { onPick: (ch: ChannelInfo) => void }) {
  const [channels, setChannels] = useState<Record<ChannelId, ChannelInfo> | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchChannels()
      .then((c) => {
        if (!cancelled) setChannels(c)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="min-h-screen relative pb-28">
      <AmbientOrbs />
      <TopAppBar status="on_air" />

      <main className="max-w-xl mx-auto px-6 pt-24 pb-12">
        <div className="mb-8 space-y-2">
          <h2 className="font-mono text-on-surface-variant uppercase tracking-[0.2em] text-[11px] font-bold">
            PICK YOUR CHANNEL
          </h2>
          <div className="h-px w-12 bg-gradient-to-r from-fuchsia-500 to-transparent" />
        </div>

        {error && (
          <div className="glass-card rounded-xl p-4 mb-6 text-sm text-red-300">
            Couldn't load channels: {error}
          </div>
        )}

        <div className="space-y-4">
          {CHANNEL_ORDER.map((id) => {
            const ui = CHANNEL_UI[id]
            const ch = channels?.[id]
            return (
              <button
                key={id}
                type="button"
                disabled={!ch}
                onClick={() => ch && onPick(ch)}
                className="glass-card w-full text-left p-5 rounded-2xl flex items-center justify-between group active:scale-[0.98] disabled:opacity-50 transition-all duration-200"
              >
                <div className="flex items-center gap-5">
                  <div
                    className={`w-12 h-12 rounded-xl ${ui.tint} ${ui.border} border flex items-center justify-center`}
                  >
                    <MSym name={ui.icon} filled={ui.iconFilled} className={ui.fg} />
                  </div>
                  <div>
                    <h3 className="font-heading text-lg text-white mb-0.5">
                      {ch?.label ?? id.replace('_', ' ')}
                    </h3>
                    <p className="font-mono text-[11px] text-on-surface-variant/60 uppercase tracking-wider">
                      4 agents · rss live
                    </p>
                  </div>
                </div>
                <MSym
                  name="chevron_right"
                  className="text-on-surface-variant/40 group-hover:translate-x-1 transition-transform"
                />
              </button>
            )
          })}
        </div>
      </main>

      <BottomNav active="sensors" />
    </div>
  )
}
