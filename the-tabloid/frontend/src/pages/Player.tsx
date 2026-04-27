import { useEffect, useRef, useState } from 'react'
import { TopAppBar } from '../components/TopAppBar'
import { BottomNav } from '../components/BottomNav'
import { AmbientOrbs } from '../components/AmbientOrbs'
import { MSym } from '../components/MSym'
import { AgentLog } from '../components/AgentLog'
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
  const frameRef = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)

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

  const [downloading, setDownloading] = useState(false)

  const downloadVideo = async () => {
    const url = segment.media_url ?? segment.video_url
    if (!url || downloading) return
    setDownloading(true)
    // Fetch to Blob so the browser actually saves it, regardless of the
    // server's Content-Disposition. Backend serves CORS `*` so this works
    // cross-origin for the dev server and Firebase Storage URLs alike.
    try {
      const resp = await fetch(url, { credentials: 'omit' })
      if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`)
      const blob = await resp.blob()
      const obj = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = obj
      const safeHead = (headline || 'segment').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)
      const ext = isAudio ? 'mp3' : 'mp4'
      a.download = `tabloid-${channel.id}-${safeHead}.${ext}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(obj), 2000)
    } catch (err) {
      // Fallback: open the raw URL in a new tab so the user can save via browser menu.
      console.error('download failed, opening in new tab', err)
      window.open(url, '_blank', 'noopener,noreferrer')
    } finally {
      setDownloading(false)
    }
  }

  const enterFullscreen = async () => {
    const v = videoRef.current as (HTMLVideoElement & {
      webkitEnterFullscreen?: () => void
    }) | null
    const frame = frameRef.current as (HTMLDivElement & {
      webkitRequestFullscreen?: () => Promise<void>
    }) | null
    if (!v) return
    // iOS Safari only exposes native video fullscreen
    if (typeof v.webkitEnterFullscreen === 'function') {
      v.webkitEnterFullscreen()
      return
    }
    // Standard Fullscreen API — wrap the frame so the chrome/ticker overlays
    // come along with the video.
    try {
      if (frame?.requestFullscreen) {
        await frame.requestFullscreen()
      } else if (frame?.webkitRequestFullscreen) {
        await frame.webkitRequestFullscreen()
      } else if (v.requestFullscreen) {
        await v.requestFullscreen()
      }
      v.play().catch(() => {})
      setPlaying(true)
    } catch {
      // user dismissed or not allowed — ignore
    }
  }

  useEffect(() => {
    const handler = () => {
      setIsFullscreen(Boolean(document.fullscreenElement))
    }
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])

  const personas = segment.personas ?? channel.default_panel
  const headline = segment.headline ?? 'Tonight on The Tabloid'
  const source = segment.source ?? channel.label
  const tickerText = `  ·  ${headline.toUpperCase()}  ·  ${source.toUpperCase()}  ·  THE TABLOID  `
  const isAudio = segment.media_kind === 'audio'

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
        {isAudio && (
          <div className="glass-card rounded-3xl overflow-hidden shadow-2xl p-6 mb-8">
            <div className="flex items-center justify-between mb-4">
              <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-fuchsia-400">
                Podcast · {channel.label}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-white/40">
                The Tabloid
              </span>
            </div>
            <h2 className="font-heading text-2xl text-white leading-tight mb-5">
              {headline}
            </h2>
            {/* Stacked persona avatars */}
            <div className="flex -space-x-3 mb-5">
              {personas.slice(0, 4).map((p) => {
                const role = ROLE_UI[p.role]
                return (
                  <div
                    key={p.id}
                    className={`w-10 h-10 rounded-full ${role.bg}/30 border-2 border-[#0b0810] flex items-center justify-center`}
                    title={`${p.name} · ${p.role}`}
                  >
                    <span className="font-display font-black text-xs text-white">
                      {p.name[0]}
                    </span>
                  </div>
                )
              })}
            </div>
            {segment.media_url ? (
              <audio
                ref={(el) => {
                  // Reuse the video ref so togglePlay + download work uniformly.
                  (videoRef as React.MutableRefObject<HTMLVideoElement | null>).current =
                    el as unknown as HTMLVideoElement
                }}
                src={segment.media_url}
                controls
                className="w-full"
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
              />
            ) : (
              <div className="font-mono text-on-surface-variant text-sm uppercase tracking-wider">
                No audio URL
              </div>
            )}
          </div>
        )}

        {/* 9:16 player frame (tabloid/video mode only) */}
        {!isAudio && (
        <div
          ref={frameRef}
          className={`relative w-full glass-card overflow-hidden shadow-2xl cursor-pointer group ${
            isFullscreen
              ? 'fixed inset-0 z-[60] aspect-auto h-screen rounded-none'
              : 'aspect-[9/16] rounded-3xl'
          }`}
          onClick={togglePlay}
        >
          {segment.video_url ? (
            <video
              ref={videoRef}
              src={segment.video_url}
              className={`absolute inset-0 w-full h-full ${isFullscreen ? 'object-contain bg-black' : 'object-cover'}`}
              playsInline
              controls={isFullscreen}
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
              <div className="flex items-center gap-2 pointer-events-auto">
                <button
                  type="button"
                  disabled={!segment.video_url || downloading}
                  onClick={(e) => {
                    e.stopPropagation()
                    downloadVideo()
                  }}
                  className="bg-black/40 backdrop-blur-md border border-white/10 rounded-full w-9 h-9 flex items-center justify-center text-white/90 hover:bg-black/60 active:scale-95 transition disabled:opacity-50"
                  aria-label="Download video"
                  title="Download"
                >
                  <MSym name={downloading ? 'hourglass_top' : 'download'} className="!text-[18px]" />
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (isFullscreen) {
                      document.exitFullscreen().catch(() => {})
                    } else {
                      enterFullscreen()
                    }
                  }}
                  className="bg-black/40 backdrop-blur-md border border-white/10 rounded-full w-9 h-9 flex items-center justify-center text-white/90 hover:bg-black/60 active:scale-95 transition"
                  aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
                >
                  <MSym name={isFullscreen ? 'fullscreen_exit' : 'fullscreen'} className="!text-[18px]" />
                </button>
                <div className="bg-[#ff0033] px-3 py-1.5 rounded-md shadow-[0_0_15px_rgba(255,0,51,0.4)]">
                  <span className="font-display font-black italic text-white text-[10px] tracking-tight uppercase">
                    The Tabloid
                  </span>
                </div>
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
        )}

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

          {(segment.media_url || segment.video_url) && (
            <button
              type="button"
              disabled={downloading}
              onClick={downloadVideo}
              className="mt-3 h-12 rounded-xl border border-white/15 bg-white/5 hover:bg-white/10 transition-all active:scale-95 font-mono text-[12px] tracking-[0.18em] uppercase text-white flex items-center justify-center gap-2 disabled:opacity-60"
            >
              <MSym name={downloading ? 'hourglass_top' : 'download'} className="!text-sm" />
              {downloading ? 'Saving…' : (isAudio ? 'Save episode.mp3' : 'Save segment.mp4')}
            </button>
          )}

          <AgentLog segment={segment} />
        </section>
      </main>

      <BottomNav active="explore" />
    </div>
  )
}
