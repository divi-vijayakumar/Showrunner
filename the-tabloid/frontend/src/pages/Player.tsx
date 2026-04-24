import { useRef, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { BottomNav } from '../components/BottomNav'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { ROLE_UI } from '../config/channelUi'
import type { ChannelInfo, Segment } from '../types'

export function Player({
  channel,
  segment,
  onNewStory,
  onNewChannel,
  onBack,
}: {
  channel: ChannelInfo
  segment: Segment
  onNewStory: () => void
  onNewChannel: () => void
  onBack: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)

  const togglePlay = () => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) {
      v.play()
      setPlaying(true)
    } else {
      v.pause()
      setPlaying(false)
    }
  }

  const personas = segment.personas ?? channel.default_panel
  const headline = segment.headline ?? 'Tonight on The Tabloid'
  const source = segment.source ?? channel.label
  const tickerText = `  ·  ${headline.toUpperCase()}  ·  ${source.toUpperCase()}  ·  THE TABLOID  `

  return (
    <div className="min-h-screen relative pb-28">
      <AmbientOrbs />
      <TopAppBar
        onBack={onBack}
        status="ready"
        centerTitle
        right={
          <button className="text-white/80" aria-label="More">
            <MSym name="more_vert" />
          </button>
        }
      />

      <main className="relative z-10 max-w-lg mx-auto px-6 pt-8 pb-32">
        {/* 9:16 player frame */}
        <div
          className="relative aspect-[9/16] w-full glass-card rounded-3xl overflow-hidden shadow-2xl cursor-pointer"
          onClick={togglePlay}
        >
          {segment.video_url ? (
            <video
              ref={videoRef}
              src={segment.video_url}
              className="absolute inset-0 w-full h-full object-cover"
              playsInline
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-surface-container">
              <span className="font-mono text-on-surface-variant text-sm uppercase tracking-wider">
                No video URL
              </span>
            </div>
          )}

          {/* Overlay chrome */}
          <div className="absolute inset-0 p-6 flex flex-col justify-between pointer-events-none">
            <div className="flex justify-between items-start">
              <div className="bg-black/40 backdrop-blur-md px-3 py-1.5 rounded-full border border-white/10">
                <span className="font-mono text-white text-[10px] tracking-wider uppercase">
                  The Tabloid · {channel.label}
                </span>
              </div>
              <div className="bg-[#ff0033] px-3 py-1.5 rounded-md shadow-[0_0_15px_rgba(255,0,51,0.4)]">
                <span className="font-display font-black italic text-white text-[10px] tracking-tight uppercase">
                  The Tabloid
                </span>
              </div>
            </div>
            {!playing && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-20 h-20 rounded-full bg-white/10 backdrop-blur-2xl border border-white/20 flex items-center justify-center">
                  <MSym name="play_arrow" filled className="!text-5xl text-white" />
                </div>
              </div>
            )}

            {/* Bottom ticker strip */}
            <div className="w-full h-10 glass-overlay -mx-6 mt-auto border-t border-white/10 flex items-center overflow-hidden">
              <div className="animate-ticker text-white/80 font-mono text-[11px] tracking-wider uppercase whitespace-nowrap">
                {tickerText}
                {tickerText}
              </div>
            </div>
          </div>
        </div>

        {/* Meta */}
        <section className="mt-8 flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-fuchsia-400 text-[11px] tracking-[0.2em] font-bold uppercase">
              {channel.label}
            </span>
            <div className="h-px flex-grow bg-white/10" />
          </div>
          <h1 className="font-heading text-3xl text-white tracking-tight leading-tight font-bold">
            {headline}
          </h1>
          {/* Persona chips */}
          <div className="flex flex-wrap gap-2 mt-2">
            {personas.map((p) => {
              const role = ROLE_UI[p.role]
              return (
                <div
                  key={p.id}
                  className={`flex items-center gap-2 bg-white/5 border border-white/10 border-l-2 ${role.border} pl-1.5 pr-3 py-1 rounded-full`}
                >
                  <div
                    className={`w-6 h-6 rounded-full ${role.bg}/30 border ${role.border}/50 flex items-center justify-center`}
                  >
                    <span className="font-display font-black text-[10px] text-white">
                      {p.name[0]}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-on-surface-variant">
                    {p.name.split(' ')[0]}
                  </span>
                </div>
              )
            })}
          </div>

          <div className="grid grid-cols-2 gap-4 mt-6">
            <button
              type="button"
              onClick={onNewChannel}
              className="h-14 rounded-xl border border-white/20 glass-overlay font-mono text-[12px] tracking-[0.18em] uppercase text-white hover:bg-white/10 transition-all active:scale-95"
            >
              New channel
            </button>
            <button
              type="button"
              onClick={onNewStory}
              className="h-14 rounded-xl bg-gradient-to-r from-purple-600 to-pink-600 font-mono text-[12px] tracking-[0.18em] uppercase text-white shadow-lg shadow-purple-500/20 hover:opacity-90 transition-all active:scale-95 flex items-center justify-center gap-2"
            >
              New story <MSym name="arrow_forward" className="!text-sm" />
            </button>
          </div>
        </section>
      </main>

      <BottomNav active="explore" />
    </div>
  )
}
