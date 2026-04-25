import { useEffect, useRef } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { AgentLog } from '../components/AgentLog'
import { useDebateStream } from '../hooks/useDebateStream'
import { useSegmentStatus } from '../hooks/useSegmentStatus'
import { ROLE_UI } from '../config/channelUi'
import type { ChannelInfo, Segment } from '../types'

export function Debate({
  channel,
  segmentId,
  onBack,
  onReady,
}: {
  channel: ChannelInfo
  segmentId: string
  onBack: () => void
  onReady: (segment: Segment) => void
}) {
  const segment = useSegmentStatus(segmentId)
  const messages = useDebateStream(segmentId)

  // Auto-scroll newest message into view
  const feedRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    feedRef.current?.scrollTo({
      top: feedRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages.length])

  // Promote to Player when ready
  useEffect(() => {
    if (segment?.status === 'ready' && segment.video_url) {
      onReady(segment)
    }
  }, [segment?.status, segment?.video_url, onReady, segment])

  const progress = segment?.progress ?? 0
  const phaseLabel =
    segment?.status === 'ready'
      ? 'READY'
      : segment?.status === 'generating'
        ? 'GENERATING · SEEDANCE 2.0'
        : segment?.status === 'debate' || segment?.status === 'queued'
          ? 'AGENTS DEBATING'
          : segment?.status === 'failed'
            ? 'FAILED'
            : 'STARTING'

  const headline = segment?.headline ?? 'Picking tonight\'s story…'

  return (
    <div className="min-h-screen relative pb-28">
      <AmbientOrbs />
      <TopAppBar onBack={onBack} status="on_air" centerTitle />

      <main className="pt-24 pb-32 px-4 md:px-8 max-w-3xl mx-auto" ref={feedRef}>
        <section className="mb-10">
          <div className="font-mono text-fuchsia-400 text-[11px] mb-2 tracking-[0.2em] uppercase">
            {channel.label}
          </div>
          <h2 className="font-display text-4xl md:text-display-xl md:text-6xl text-white leading-[1.05] font-black">
            {headline}
          </h2>
          <div className="h-1 w-24 mt-5 bg-gradient-to-r from-purple-500 to-pink-500 rounded-full" />
        </section>

        <div className="space-y-5">
          {messages.length === 0 && (
            <div className="glass-card rounded-xl p-6 text-sm text-on-surface-variant animate-pulseDot">
              Panel is warming up…
            </div>
          )}
          {messages.map((m) => {
            const role = ROLE_UI[m.agent]
            return (
              <article
                key={m.seq}
                className={`glass-card p-5 rounded-xl border-t-4 ${role.topBorder} relative overflow-hidden group`}
              >
                <div className="flex items-start gap-4">
                  <div className="relative shrink-0">
                    <div
                      className={`w-12 h-12 rounded-lg ${role.bg}/20 border ${role.border}/40 flex items-center justify-center`}
                    >
                      <span className="font-display font-black text-sm text-white">
                        {m.persona_name
                          .split(' ')
                          .slice(0, 2)
                          .map((w) => w[0])
                          .join('')
                          .toUpperCase()}
                      </span>
                    </div>
                    <span
                      className={`absolute -bottom-1 -right-1 ${role.bg} text-[8px] font-mono px-1.5 py-0.5 rounded text-white shadow-lg ${role.glow} uppercase`}
                    >
                      {role.label}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-2 gap-3">
                      <span className="font-mono text-white text-sm font-bold truncate">
                        {m.persona_name}
                      </span>
                      <span className="font-mono text-white/30 text-[10px] uppercase shrink-0">
                        {m.persona_lean}
                      </span>
                    </div>
                    <p className="font-body text-body-lg text-on-surface leading-relaxed">
                      {m.content}
                    </p>
                  </div>
                </div>
              </article>
            )
          })}
        </div>

        {segment?.status === 'failed' && (
          <div className="glass-card mt-6 p-5 rounded-xl border border-red-500/30">
            <p className="font-mono text-xs text-red-400 uppercase tracking-wider mb-1">
              Pipeline failed
            </p>
            <p className="font-body text-sm text-on-surface-variant">
              {segment.error ?? 'Unknown error'}
            </p>
          </div>
        )}

        {segment && <AgentLog segment={segment} />}
      </main>

      {/* Persistent progress footer */}
      <footer className="fixed bottom-0 left-0 w-full z-40">
        <div className="w-full h-1 bg-white/10">
          <div
            className="h-full bg-gradient-to-r from-purple-500 to-cyan-400 transition-all duration-700"
            style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
          />
        </div>
        <div
          className="bg-white/5 backdrop-blur-2xl border-t border-white/10 h-20 flex items-center justify-between px-6"
          style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        >
          <div className="flex flex-col min-w-0">
            <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-white/50 truncate">
              {phaseLabel}
            </span>
            <span className="font-mono text-cyan-400 text-lg font-bold">{progress}%</span>
          </div>
          <div className="flex items-center gap-5 text-white/40">
            <MSym name="sensors" filled />
            <MSym name="explore" className="text-fuchsia-500" />
            <MSym name="chat_bubble" />
            <MSym name="person" />
          </div>
        </div>
      </footer>
    </div>
  )
}
