import { useEffect, useMemo, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { ROLE_UI, CHANNEL_UI } from '../config/channelUi'
import { castForStory } from '../api'
import type { ChannelInfo, Persona, StoryCandidate } from '../types'

type CastingState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; fallback: string | null; brief?: Record<string, unknown> | null }
  | { status: 'error'; message: string }

export function PersonaSelect({
  channel,
  story,
  onBack,
  onStart,
}: {
  channel: ChannelInfo
  // The picked story. When provided, we run the casting agent BEFORE
  // showing the panel so "Meet your panel" reflects who is actually
  // going to debate (not the static personas.py defaults). When null
  // (auto-pick path), the casting agent runs server-side after Start.
  story: StoryCandidate | null
  onBack: () => void
  onStart: (panel: Persona[], brief?: Record<string, unknown> | null) => void
}) {
  // Default panel as the initial state — used until casting lands, and
  // as the auto-pick fallback when no story is provided.
  const [panel, setPanel] = useState<Persona[]>(channel.default_panel)
  // Track which panel we should treat as the "user hasn't swapped" baseline
  // — starts as default_panel, gets replaced by the LLM cast result.
  const [baseline, setBaseline] = useState<Persona[]>(channel.default_panel)
  const [casting, setCasting] = useState<CastingState>({ status: 'idle' })
  const [brief, setBrief] = useState<Record<string, unknown> | null>(null)

  const accent = CHANNEL_UI[channel.id].accentGradient

  useEffect(() => {
    if (!story) {
      // Auto-pick path — no specific story to cast against; let the server
      // pick + cast after Start.
      setCasting({ status: 'idle' })
      return
    }
    let cancelled = false
    setCasting({ status: 'loading' })
    castForStory(channel.id, story)
      .then((res) => {
        if (cancelled) return
        if (res.personas && res.personas.length === 4) {
          setPanel(res.personas)
          setBaseline(res.personas)
        }
        setBrief(res.story_brief ?? null)
        setCasting({ status: 'ready', fallback: res.fallback, brief: res.story_brief })
      })
      .catch((e) => {
        if (!cancelled) setCasting({ status: 'error', message: String(e) })
      })
    return () => {
      cancelled = true
    }
  }, [channel.id, story])

  const swap = (idx: number) => {
    const role = panel[idx].role
    const candidates = [
      baseline[idx],
      ...channel.alternatives.filter((p) => p.role === role),
    ]
    const currentId = panel[idx].id
    const currentPos = candidates.findIndex((p) => p.id === currentId)
    const next = candidates[(currentPos + 1) % candidates.length]
    setPanel((prev) => prev.map((p, i) => (i === idx ? next : p)))
  }

  const headline = useMemo(() => `${channel.label} · live debate`, [channel.label])
  const isLoading = casting.status === 'loading'
  const llmFell = casting.status === 'ready' && casting.fallback != null
  const llmPicked = casting.status === 'ready' && casting.fallback == null

  return (
    <div className="min-h-screen relative pb-40">
      <AmbientOrbs />
      <TopAppBar onBack={onBack} status="on_air" centerTitle />

      <main className="pt-24 px-6 max-w-2xl mx-auto space-y-8">
        <section className="space-y-2">
          <h2 className="font-heading text-h1 text-white leading-tight">Meet your panel</h2>
          <p className="font-mono text-[11px] text-fuchsia-400 uppercase tracking-[0.2em]">
            {headline}
          </p>
          {isLoading && (
            <p className="font-mono text-[11px] text-emerald-300 uppercase tracking-[0.2em] flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              Casting agent picking story-relevant guests…
            </p>
          )}
          {llmPicked && (
            <p className="font-mono text-[11px] text-emerald-300 uppercase tracking-[0.2em]">
              ✓ Panel cast for this story
            </p>
          )}
          {llmFell && (
            <p className="font-mono text-[11px] text-amber-400 uppercase tracking-[0.2em]">
              Casting fell back to defaults ({(casting as { fallback: string }).fallback})
            </p>
          )}
        </section>

        {/* Story pill — show the real headline if we have one, otherwise channel label */}
        <section className="glass-pill rounded-full p-1.5 flex items-center pr-6 gap-4">
          <div className="bg-white text-black font-mono text-[10px] px-3 py-1.5 rounded-full font-bold uppercase">
            {channel.label}
          </div>
          <h3 className="font-body text-body-md text-on-surface font-semibold truncate">
            {story?.title ?? "Your story will be picked live from today's feed"}
          </h3>
        </section>

        <section className="space-y-4">
          {panel.map((p, idx) => {
            const role = ROLE_UI[p.role]
            return (
              <div
                key={`${p.id}-${idx}`}
                className={`glass-card rounded-2xl p-4 flex items-center justify-between border-l-4 ${role.border} hover:scale-[1.01] transition-transform group ${
                  isLoading ? 'animate-pulse opacity-60' : ''
                }`}
              >
                <div className="flex items-center gap-4 min-w-0">
                  <div className="relative shrink-0">
                    <div
                      className={`w-14 h-14 rounded-xl ${role.bg}/20 border ${role.border}/40 flex items-center justify-center`}
                    >
                      <span className="font-display font-black text-lg text-white">
                        {p.name
                          .split(' ')
                          .slice(0, 2)
                          .map((w) => w[0])
                          .join('')
                          .toUpperCase()}
                      </span>
                    </div>
                    <div
                      className={`absolute -bottom-1 -right-1 ${role.bg} text-[8px] font-mono px-1.5 py-0.5 rounded-full text-white shadow-lg ${role.glow}`}
                    >
                      {role.label}
                    </div>
                  </div>
                  <div className="min-w-0">
                    <h4 className="font-heading text-lg text-white leading-tight truncate">
                      {p.name}
                    </h4>
                    <p className="font-body text-sm text-on-surface-variant truncate">
                      {p.lean}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => swap(idx)}
                  disabled={isLoading}
                  className="shrink-0 p-2 rounded-full text-on-surface-variant hover:text-white active:scale-90 transition-all disabled:opacity-40"
                  aria-label={`Swap ${p.role}`}
                >
                  <MSym name="swap_horiz" />
                </button>
              </div>
            )
          })}
        </section>
      </main>

      {/* Fixed bottom CTA */}
      <div className="fixed bottom-0 left-0 w-full p-6 bg-gradient-to-t from-canvas via-canvas/90 to-transparent z-40">
        <button
          type="button"
          onClick={() => onStart(panel, brief)}
          disabled={isLoading}
          className={`w-full bg-gradient-to-r ${accent} text-white font-heading text-lg py-5 rounded-2xl flex items-center justify-center gap-3 shadow-2xl shadow-purple-500/20 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-wait`}
        >
          {isLoading ? 'Casting your panel…' : 'Start the debate'}
          {!isLoading && <MSym name="arrow_forward" />}
        </button>
      </div>
    </div>
  )
}
