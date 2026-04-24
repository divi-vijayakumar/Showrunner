import { useMemo, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { ROLE_UI, CHANNEL_UI } from '../config/channelUi'
import type { ChannelInfo, Persona } from '../types'

export function PersonaSelect({
  channel,
  onBack,
  onStart,
}: {
  channel: ChannelInfo
  onBack: () => void
  onStart: (panel: Persona[]) => void
}) {
  const initial = channel.default_panel
  const [panel, setPanel] = useState<Persona[]>(initial)

  const accent = CHANNEL_UI[channel.id].accentGradient

  const swap = (idx: number) => {
    const role = panel[idx].role
    // Cycle through alternatives of the same role (plus the original default)
    const candidates = [
      initial[idx],
      ...channel.alternatives.filter((p) => p.role === role),
    ]
    const currentId = panel[idx].id
    const currentPos = candidates.findIndex((p) => p.id === currentId)
    const next = candidates[(currentPos + 1) % candidates.length]
    setPanel((prev) => prev.map((p, i) => (i === idx ? next : p)))
  }

  const headline = useMemo(() => `${channel.label} · live debate`, [channel.label])

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
        </section>

        {/* Story pill — channel label as pseudo-source */}
        <section className="glass-pill rounded-full p-1.5 flex items-center pr-6 gap-4">
          <div className="bg-white text-black font-mono text-[10px] px-3 py-1.5 rounded-full font-bold uppercase">
            {channel.label}
          </div>
          <h3 className="font-body text-body-md text-on-surface font-semibold truncate">
            Your story will be picked live from today's feed
          </h3>
        </section>

        <section className="space-y-4">
          {panel.map((p, idx) => {
            const role = ROLE_UI[p.role]
            return (
              <div
                key={p.id}
                className={`glass-card rounded-2xl p-4 flex items-center justify-between border-l-4 ${role.border} hover:scale-[1.01] transition-transform group`}
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
                  className="shrink-0 p-2 rounded-full text-on-surface-variant hover:text-white active:scale-90 transition-all"
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
          onClick={() => onStart(panel)}
          className={`w-full bg-gradient-to-r ${accent} text-white font-heading text-lg py-5 rounded-2xl flex items-center justify-center gap-3 shadow-2xl shadow-purple-500/20 active:scale-[0.98] transition-all`}
        >
          Start the debate
          <MSym name="arrow_forward" />
        </button>
      </div>
    </div>
  )
}
