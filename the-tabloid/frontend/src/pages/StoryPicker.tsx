import { useEffect, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { CHANNEL_UI } from '../config/channelUi'
import { apiBase, fetchChannelStories, startDirect } from '../api'
import type { ChannelInfo, StoryCandidate } from '../types'

export function StoryPicker({
  channel,
  onBack,
  onPick,
  onAutoPick,
}: {
  channel: ChannelInfo
  onBack: () => void
  onPick: (story: StoryCandidate) => void
  onAutoPick: () => void
}) {
  const [stories, setStories] = useState<StoryCandidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [firing, setFiring] = useState(false)
  const ui = CHANNEL_UI[channel.id]

  useEffect(() => {
    let cancelled = false
    fetchChannelStories(channel.id, 8)
      .then((d) => {
        if (!cancelled) setStories(d.stories)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [channel.id])

  // Pinned stories with `direct_script` route through /api/generate-direct
  // instead of the regular RSS+casting+debate+video flow. They use the
  // hand-authored script JSON for story/cast/scenes/director output and
  // share execution+editing+rendering with the production pipeline.
  const handleStoryClick = async (s: StoryCandidate) => {
    if (!s.direct_script) {
      onPick(s)
      return
    }
    if (firing) return
    const ok = window.confirm(
      `Fire fresh "${s.direct_script}" run?\n\n` +
        `This burns ~$16 of Seedance credit and takes ~10 min.\n` +
        `The /direct/{seg_id} player opens in this tab and shows ` +
        `each scene as it lands, then offers a "Build final episode" ` +
        `button once they're all ready.`,
    )
    if (!ok) return
    try {
      setFiring(true)
      const { segment_id } = await startDirect(s.direct_script)
      window.location.href = `${apiBase}/direct/${segment_id}`
    } catch (e) {
      setFiring(false)
      window.alert(`Direct-mode start failed: ${e}`)
    }
  }

  return (
    <div className="min-h-screen relative pb-32">
      <AmbientOrbs />
      <TopAppBar onBack={onBack} status="on_air" centerTitle />

      <main className="max-w-xl mx-auto px-6 pt-24 pb-24">
        <div className="mb-6 space-y-2">
          <span className={`font-mono text-[11px] tracking-[0.25em] uppercase ${ui.fg}`}>
            {channel.label}
          </span>
          <h2 className="font-heading text-3xl text-white tracking-tight font-bold">
            Pick a story
          </h2>
          <p className="font-body text-sm text-on-surface-variant/80">
            Eight live headlines from this channel's RSS pool. Tap one to debate it,
            or let the agent decide.
          </p>
        </div>

        <button
          type="button"
          onClick={onAutoPick}
          className="mb-6 w-full glass-card rounded-2xl p-4 flex items-center justify-between border border-fuchsia-500/30 hover:border-fuchsia-500/60 transition-colors active:scale-[0.99]"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-fuchsia-500/20 border border-fuchsia-500/40 flex items-center justify-center">
              <MSym name="auto_awesome" filled className="!text-[18px] text-fuchsia-300" />
            </div>
            <div className="text-left">
              <div className="font-mono text-[11px] uppercase tracking-[0.18em] text-fuchsia-300">
                Auto-pick
              </div>
              <div className="font-body text-sm text-white/90">
                Let the agent choose the most debatable story
              </div>
            </div>
          </div>
          <MSym name="chevron_right" className="text-fuchsia-300/60" />
        </button>

        {error && (
          <div className="glass-card rounded-xl p-4 mb-6 text-sm text-red-300">
            Couldn't load stories: {error}
          </div>
        )}

        {!stories && !error && (
          <div className="space-y-3">
            {[1, 2, 3, 4, 5].map((k) => (
              <div
                key={k}
                className="glass-card rounded-2xl p-5 animate-pulse h-24 opacity-50"
              />
            ))}
          </div>
        )}

        {stories && stories.length === 0 && (
          <div className="glass-card rounded-xl p-4 text-sm text-on-surface-variant">
            No live stories from this channel right now. Try Auto-pick.
          </div>
        )}

        <div className="space-y-3">
          {stories?.map((s) => {
            const isDirect = !!s.direct_script
            const disabled = firing
            return (
              <button
                key={s.link || s.title}
                type="button"
                disabled={disabled}
                onClick={() => handleStoryClick(s)}
                className={`glass-card w-full text-left p-5 rounded-2xl group active:scale-[0.99] transition-all duration-200 hover:border-white/30 ${
                  isDirect ? 'border border-emerald-500/40 hover:border-emerald-500/60' : ''
                } ${disabled ? 'opacity-50 cursor-wait' : ''}`}
              >
                <div className="flex items-center gap-2 mb-2 flex-wrap">
                  <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-on-surface-variant/70">
                    {s.source}
                  </span>
                  {s.pinned && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30">
                      Pinned
                    </span>
                  )}
                  {isDirect && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                      Direct mode
                    </span>
                  )}
                  {s.has_body && !isDirect && (
                    <span className="font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                      Full text
                    </span>
                  )}
                </div>
                <h3 className="font-heading text-base text-white leading-snug mb-1.5 font-semibold">
                  {s.title}
                </h3>
                {s.body_preview && (
                  <p className="font-body text-[13px] text-on-surface-variant/80 leading-relaxed line-clamp-2">
                    {s.body_preview.slice(0, 220)}
                    {s.body_preview.length > 220 ? '…' : ''}
                  </p>
                )}
                {isDirect && (
                  <p className="font-mono text-[10px] uppercase tracking-wider text-emerald-300/80 mt-2">
                    Hand-authored script · ~$16 · ~10 min · scene-by-scene player
                  </p>
                )}
              </button>
            )
          })}
        </div>
      </main>
    </div>
  )
}
