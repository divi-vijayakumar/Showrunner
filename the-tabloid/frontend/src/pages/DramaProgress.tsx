import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSegmentStatus } from '../hooks/useSegmentStatus'
import { cancelDrama } from '../api'
import type { Segment } from '../types'

interface Props {
  segmentId: string
  onBack: () => void
}

interface PipelineStep {
  name: string
  status: 'pending' | 'in_progress' | 'done' | 'skipped' | 'failed'
  detail?: string
  started_at?: number
  completed_at?: number
}

interface LogLine {
  ts: number
  level: 'info' | 'warn' | 'error'
  message: string
  stage?: string
}

interface CastingRow {
  scene_number: number
  title?: string
  speaker_label: string
  speaker_anchor_url?: string | null
  set_label: string
  set_anchor_url?: string | null
  visible_characters: string[]
  visible_character_urls: Record<string, string>
}

interface DirectionRow {
  scene_number: number
  title?: string
  duration_s?: number
  language?: string
  camera_motion?: string
  audio_direction?: string
  spoken_line?: string
  seedance_prompt: string
}

interface SceneStatus {
  scene_number: number
  status: 'ready' | 'placeholder' | 'pending'
  clip_url?: string
  error?: string
}

interface DramaSegment extends Segment {
  pipeline_steps?: PipelineStep[]
  log_lines?: LogLine[]
  character_urls?: Record<string, string>           // Pixarified
  character_urls_original?: Record<string, string>  // raw uploads
  set_urls_uploaded?: Record<string, string>           // Pixarified
  set_urls_uploaded_original?: Record<string, string>  // raw uploads
  set_urls_generated?: Record<string, string>
  set_urls?: Record<string, string>
  scene_plan?: {
    title?: string
    scenes?: { scene_number: number; title?: string; duration_s?: number; language?: string;
              spoken_line?: string; speaker_label?: string; set_label?: string;
              camera_motion?: string; visual_description?: string; audio_direction?: string;
              visible_characters?: string[] }[]
    sets_to_generate?: { id: string; description: string }[]
    outro_card?: { text: string; duration_s?: number } | null
  }
  casting_table?: CastingRow[]
  direction_table?: DirectionRow[]
  scene_status?: Record<string, SceneStatus>
  drama_brief?: string
  default_language?: string
}

export function DramaProgress({ segmentId, onBack }: Props) {
  const segment = useSegmentStatus(segmentId) as DramaSegment | null

  useEffect(() => {
    if (segment?.headline) document.title = `🎬 ${segment.headline}`
    return () => { document.title = 'The Tabloid' }
  }, [segment?.headline])

  const steps = segment?.pipeline_steps || []
  const logLines = segment?.log_lines || []
  const progress = segment?.progress ?? 0
  const phase = segment?.status ?? 'queued'
  const videoUrl = segment?.media_url || segment?.video_url
  const [cancelling, setCancelling] = useState(false)
  const onCancel = useCallback(async () => {
    if (!window.confirm(
      'Cancel this drama? Already-issued ARK tasks finish on their own (paid for); no new tasks will fire.',
    )) return
    try {
      setCancelling(true)
      await cancelDrama(segmentId)
    } catch (e) {
      window.alert(`Cancel failed: ${(e as Error).message}`)
    } finally {
      setCancelling(false)
    }
  }, [segmentId])
  const isTerminal = phase === 'ready' || phase === 'failed' || phase === 'cancelled'

  const sceneStatuses = useMemo(() => {
    const raw = segment?.scene_status || {}
    return Object.entries(raw)
      .map(([k, v]) => ({ index: Number(k), ...v }))
      .sort((a, b) => a.index - b.index)
  }, [segment?.scene_status])

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <button onClick={onBack} style={styles.backBtn}>← New drama</button>
        {!isTerminal && (
          <button
            onClick={onCancel}
            disabled={cancelling}
            style={styles.cancelBtn}
          >
            {cancelling ? 'Cancelling…' : '✕ Cancel this run'}
          </button>
        )}
        <h1 style={styles.title}>{segment?.headline || 'Short Drama'}</h1>
        <span style={{
          ...styles.phasePill,
          background: phase === 'failed' ? '#ff4040'
            : phase === 'ready' ? '#3bd28b'
              : '#fa3e3e',
        }}>{(phase as string).toUpperCase()}</span>
      </header>

      <main style={styles.main}>
        <section style={styles.progressBlock}>
          <div style={styles.progressBarOuter}>
            <div style={{
              ...styles.progressBarInner,
              width: `${Math.min(100, Math.max(0, progress))}%`,
              background: phase === 'failed' ? '#ff6060' : phase === 'ready' ? '#3bd28b' : '#fa3e3e',
            }} />
          </div>
          <div style={styles.progressMeta}>
            <span>Pipeline · {steps.filter(s => s.status === 'done').length}/{steps.length} stages complete</span>
            <span>{Math.round(progress)}%</span>
          </div>
        </section>

        {segment?.error && (
          <section style={styles.errorBox}>
            <strong>Error:</strong> {segment.error}
          </section>
        )}

        {videoUrl && (
          <section style={styles.videoBlock}>
            <h2 style={styles.h2}>🎬 Final film</h2>
            <video src={videoUrl} controls autoPlay playsInline style={styles.video} />
            <a href={videoUrl} download style={styles.downloadBtn}>Download mp4</a>
          </section>
        )}

        <section style={styles.stagesList}>
          {steps.map((step, i) => (
            <StageCard
              key={step.name}
              step={step}
              segment={segment}
              sceneStatuses={sceneStatuses}
              defaultOpen={step.status === 'in_progress' || (i === 0 && steps.every(s => s.status === 'pending'))}
            />
          ))}
        </section>

        <section>
          <h2 style={styles.h2}>📜 Live log</h2>
          <LogPanel lines={logLines} />
        </section>
      </main>
    </div>
  )
}

function StageCard({
  step,
  segment,
  sceneStatuses,
  defaultOpen,
}: {
  step: PipelineStep
  segment: DramaSegment | null
  sceneStatuses: (SceneStatus & { index: number })[]
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(!!defaultOpen)
  useEffect(() => {
    // Auto-open while in progress, OR (for Video Generation specifically)
    // whenever at least one scene clip has landed — the user wants to watch
    // them as they ship.
    if (step.status === 'in_progress') setOpen(true)
    if ((step.name === 'Production' || step.name === 'Video Generation')
        && sceneStatuses.some(s => s.clip_url)) {
      setOpen(true)
    }
  }, [step.status, step.name, sceneStatuses])

  const dot = step.status === 'done' ? '#3bd28b'
    : step.status === 'in_progress' ? '#ffd24a'
      : step.status === 'failed' ? '#ff4040'
        : step.status === 'skipped' ? '#7a7a86'
          : '#3a3a4a'

  const symbol = step.status === 'done' ? '✓'
    : step.status === 'in_progress' ? '●'
      : step.status === 'failed' ? '✗'
        : step.status === 'skipped' ? '↷'
          : '○'

  const elapsed = step.started_at && step.completed_at
    ? `${(step.completed_at - step.started_at).toFixed(1)}s`
    : step.started_at && !step.completed_at
      ? '⋯'
      : null

  return (
    <div style={{
      ...styles.stageCard,
      borderColor: step.status === 'in_progress' ? '#5a5aff' : '#1f1f28',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={styles.stageHeader}
      >
        <span style={{ ...styles.stageDot, background: dot, color: '#0b0b0f' }}>{symbol}</span>
        <span style={styles.stageName}>{step.name}</span>
        {step.detail && <span style={styles.stageDetail}>{step.detail}</span>}
        {elapsed && <span style={styles.stageElapsed}>{elapsed}</span>}
        <span style={styles.stageChevron}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div style={styles.stageBody}>
          <StageOutput step={step} segment={segment} sceneStatuses={sceneStatuses} />
        </div>
      )}
    </div>
  )
}

function StageOutput({
  step,
  segment,
  sceneStatuses,
}: {
  step: PipelineStep
  segment: DramaSegment | null
  sceneStatuses: (SceneStatus & { index: number })[]
}) {
  if (!segment) return <div style={styles.empty}>(waiting for backend…)</div>

  switch (step.name) {
    case 'Script':
      return <ScriptOutput segment={segment} />
    case 'Casting':
      return (
        <>
          <AssetGenerationOutput segment={segment} />
          <CastingOutput segment={segment} />
        </>
      )
    case 'Direction':
      return <DirectionOutput segment={segment} />
    case 'Production':
      return <VideoGenerationOutput segment={segment} sceneStatuses={sceneStatuses} />
    case 'Editing':
      return <FinalOutputOutput segment={segment} />
    // Back-compat for in-flight runs that pre-date the rename
    case 'Asset Generation':
      return <AssetGenerationOutput segment={segment} />
    case 'Video Generation':
      return <VideoGenerationOutput segment={segment} sceneStatuses={sceneStatuses} />
    case 'Final Output':
      return <FinalOutputOutput segment={segment} />
    default:
      return <div style={styles.empty}>(no output)</div>
  }
}

function AssetGenerationOutput({ segment }: { segment: DramaSegment }) {
  const charsPixar = Object.entries(segment.character_urls || {})
  const charsOrig = segment.character_urls_original || {}
  const setsUpPixar = Object.entries(segment.set_urls_uploaded || {})
  const setsUpOrig = segment.set_urls_uploaded_original || {}
  const setsGen = Object.entries(segment.set_urls_generated || {})
  if (!charsPixar.length && !setsUpPixar.length && !setsGen.length) {
    return <div style={styles.empty}>No assets yet (Pixarification in flight)</div>
  }
  return (
    <div>
      <p style={styles.assetExplain}>
        Each uploaded photo runs through ARK Seedream <strong>i2i</strong> (image-to-image)
        with an aggressive cartoon prompt — preserves face shape / wardrobe palette while
        re-rendering as a 3D-animated cartoon. The Pixar version (right) is what goes to
        Seedance as <code>reference_image</code>; the original (left) stays on disk for context.
      </p>
      {charsPixar.length > 0 && (
        <>
          <h4 style={styles.subhead}>Characters — original → Pixarified</h4>
          <div style={styles.assetGrid}>
            {charsPixar.map(([label, pixarUrl]) => (
              <figure key={label} style={styles.assetPair}>
                <div style={styles.pairRow}>
                  {charsOrig[label] && (
                    <img src={charsOrig[label]} alt={`${label} original`}
                         style={styles.pairImg} title="original" />
                  )}
                  <span style={styles.pairArrow}>→</span>
                  <img src={pixarUrl} alt={`${label} Pixar`}
                       style={styles.pairImg} title="Pixarified (sent to Seedance)" />
                </div>
                <figcaption style={styles.assetCaption}>{label}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {setsUpPixar.length > 0 && (
        <>
          <h4 style={styles.subhead}>Sets — original → Pixarified</h4>
          <div style={styles.assetGrid}>
            {setsUpPixar.map(([label, pixarUrl]) => (
              <figure key={label} style={styles.assetPair}>
                <div style={styles.pairRow}>
                  {setsUpOrig[label] && (
                    <img src={setsUpOrig[label]} alt={`${label} original`}
                         style={styles.pairImg} title="original" />
                  )}
                  <span style={styles.pairArrow}>→</span>
                  <img src={pixarUrl} alt={`${label} Pixar`}
                       style={styles.pairImg} title="Pixarified" />
                </div>
                <figcaption style={styles.assetCaption}>{label}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {setsGen.length > 0 && (
        <>
          <h4 style={styles.subhead}>Pixar set masters generated by ARK Seedream t2i</h4>
          <div style={styles.assetGrid}>
            {setsGen.map(([id, url]) => (
              <figure key={id} style={styles.assetCard}>
                <img src={url} alt={id} style={styles.assetImg} />
                <figcaption style={styles.assetCaption}>{id}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function CastingOutput({ segment }: { segment: DramaSegment }) {
  const rows = segment.casting_table || []
  if (!rows.length) return <div style={styles.empty}>(casting table will appear after the script stage)</div>
  return (
    <div style={styles.castingList}>
      {rows.map((r) => (
        <div key={r.scene_number} style={styles.castRow}>
          <div style={styles.castSceneNum}>#{r.scene_number}</div>
          <div style={{ flex: 1 }}>
            <div style={styles.castTitle}>{r.title}</div>
            <div style={styles.castMeta}>
              <span><strong>Speaker:</strong> {r.speaker_label}</span>
              <span><strong>Set:</strong> {r.set_label}</span>
            </div>
          </div>
          <div style={styles.castThumbs}>
            {r.speaker_anchor_url && (
              <img src={r.speaker_anchor_url} alt={r.speaker_label} style={styles.castThumb} title={`speaker: ${r.speaker_label}`} />
            )}
            {r.set_anchor_url && (
              <img src={r.set_anchor_url} alt={r.set_label} style={styles.castThumb} title={`set: ${r.set_label}`} />
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function ScriptOutput({ segment }: { segment: DramaSegment }) {
  const plan = segment.scene_plan
  if (!plan?.scenes?.length) return <div style={styles.empty}>(writer is still composing…)</div>
  return (
    <div>
      <div style={styles.scriptHeader}>
        <span style={styles.scriptTitle}>{plan.title || 'Untitled'}</span>
        {plan.outro_card?.text && <span style={styles.outroChip}>Outro: {plan.outro_card.text}</span>}
      </div>
      <ol style={styles.scriptList}>
        {plan.scenes.map((sc) => (
          <li key={sc.scene_number} style={styles.scriptScene}>
            <div style={styles.scriptSceneHead}>
              <span style={styles.scriptSceneNum}>SCENE {sc.scene_number}</span>
              <span style={styles.scriptSceneTitle}>{sc.title}</span>
              <span style={styles.scriptBadge}>{sc.duration_s}s</span>
              <span style={{ ...styles.scriptBadge, color: '#ffd24a' }}>{sc.language}</span>
              <span style={styles.scriptBadge}>{sc.camera_motion}</span>
            </div>
            {sc.visual_description && (
              <div style={styles.scriptDesc}>{sc.visual_description}</div>
            )}
            {sc.spoken_line && (
              <div style={styles.scriptLine}>
                <strong>{sc.speaker_label}:</strong> "{sc.spoken_line}"
              </div>
            )}
            {sc.audio_direction && (
              <div style={styles.scriptAudio}>🔊 {sc.audio_direction}</div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

function DirectionOutput({ segment }: { segment: DramaSegment }) {
  const rows = segment.direction_table || []
  if (!rows.length) return <div style={styles.empty}>(director will compose prompts after casting)</div>
  return (
    <div>
      {rows.map((r) => (
        <details key={r.scene_number} style={styles.directionScene}>
          <summary style={styles.directionSummary}>
            <strong>SCENE {r.scene_number}</strong> · {r.title}
            <span style={styles.scriptBadge}>{r.duration_s}s</span>
            <span style={{ ...styles.scriptBadge, color: '#ffd24a' }}>{r.language}</span>
            <span style={styles.directionPromptLen}>{r.seedance_prompt.length} chars</span>
          </summary>
          <pre style={styles.directionPrompt}>{r.seedance_prompt}</pre>
        </details>
      ))}
    </div>
  )
}

function VideoGenerationOutput({
  segment,
  sceneStatuses,
}: {
  segment: DramaSegment
  sceneStatuses: (SceneStatus & { index: number })[]
}) {
  const scenes = segment.scene_plan?.scenes || []
  if (!scenes.length) return <div style={styles.empty}>(no scenes yet)</div>
  return (
    <div style={styles.clipGrid}>
      {scenes.map((sc) => {
        const st = sceneStatuses.find((s) => s.scene_number === sc.scene_number)
        return (
          <div key={sc.scene_number} style={styles.clipTile}>
            <div style={styles.clipHead}>
              <span>#{sc.scene_number}</span>
              <span style={styles.scriptBadge}>{sc.duration_s}s</span>
              <span style={{
                ...styles.clipDot,
                background: !st ? '#3a3a4a'
                  : st.status === 'ready' ? '#3bd28b'
                    : st.status === 'placeholder' ? '#ff8a3a'
                      : '#ffd24a',
              }} />
            </div>
            <div style={styles.clipTitle}>{sc.title}</div>
            {st?.clip_url && (
              <video src={st.clip_url} controls playsInline preload="metadata" style={styles.clipVideo} />
            )}
            {!st?.clip_url && (
              <div style={styles.clipPending}>
                {st?.status === 'placeholder' ? '✗ failed (placeholder)' : st ? '⋯ generating…' : '○ queued'}
              </div>
            )}
            {st?.error && <div style={styles.clipErr}>{st.error}</div>}
          </div>
        )
      })}
    </div>
  )
}

function FinalOutputOutput({ segment }: { segment: DramaSegment }) {
  const url = segment.media_url || segment.video_url
  if (!url) return <div style={styles.empty}>(stitch in progress)</div>
  return (
    <div>
      <video src={url} controls playsInline preload="metadata" style={styles.video} />
      <a href={url} download style={styles.downloadBtn}>Download mp4</a>
    </div>
  )
}

function LogPanel({ lines }: { lines: LogLine[] }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines.length])
  return (
    <div ref={ref} style={styles.logPanel}>
      {lines.length === 0 && <div style={styles.empty}>(no log yet)</div>}
      {lines.map((l, i) => (
        <div key={i} style={{
          ...styles.logLine,
          color: l.level === 'error' ? '#ff8a8a' : l.level === 'warn' ? '#ffd24a' : '#c5c5cf',
        }}>
          <span style={styles.logTime}>{new Date((l.ts || 0) * 1000).toLocaleTimeString()}</span>
          {l.stage && <span style={styles.logStageTag}>[{l.stage}]</span>}
          <span>{l.message}</span>
        </div>
      ))}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { background: '#0b0b0f', color: '#e6e6ea', minHeight: '100vh', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif' },
  header: { padding: '20px 32px', borderBottom: '1px solid #1a1a22', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' },
  backBtn: { background: 'transparent', border: '1px solid #2a2a36', color: '#c5c5cf', padding: '6px 12px', borderRadius: 4, cursor: 'pointer', fontSize: 13 },
  cancelBtn: { background: '#3a1f1f', border: '1px solid #5a2828', color: '#ff8a8a', padding: '6px 12px', borderRadius: 4, cursor: 'pointer', fontSize: 13 },
  title: { margin: 0, fontSize: 20, color: '#e6e6ea', fontWeight: 600 },
  phasePill: { marginLeft: 'auto', color: 'white', padding: '4px 10px', borderRadius: 3, fontSize: 11, letterSpacing: '0.06em', fontWeight: 700 },
  main: { maxWidth: 1100, margin: '0 auto', padding: '24px 32px 48px', display: 'flex', flexDirection: 'column', gap: 22 },

  progressBlock: { display: 'flex', flexDirection: 'column', gap: 6 },
  progressBarOuter: { background: '#1a1a22', borderRadius: 99, height: 10, overflow: 'hidden' },
  progressBarInner: { height: '100%', borderRadius: 99, transition: 'width 0.4s ease' },
  progressMeta: { display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#8a8a96' },

  errorBox: { background: '#2a1a1a', border: '1px solid #5a2828', color: '#ff8a8a', padding: '12px 16px', borderRadius: 4, fontSize: 13 },

  videoBlock: { display: 'flex', flexDirection: 'column', gap: 10, padding: 14, background: '#15151c', borderRadius: 8, border: '1px solid #2a2a36' },
  h2: { margin: '4px 0', fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#8a8a96', fontWeight: 600 },
  video: { width: '100%', maxHeight: '70vh', background: 'black', borderRadius: 6 },
  downloadBtn: { display: 'inline-block', background: '#1a1a22', color: '#7e7eff', textDecoration: 'none', padding: '6px 14px', borderRadius: 4, fontSize: 13, border: '1px solid #2a2a36', alignSelf: 'flex-start', marginTop: 8 },

  stagesList: { display: 'flex', flexDirection: 'column', gap: 10 },
  stageCard: { background: '#15151c', border: '1px solid', borderRadius: 6, overflow: 'hidden' },
  stageHeader: { width: '100%', background: 'transparent', border: 0, color: '#e6e6ea', padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', textAlign: 'left', fontSize: 14 },
  stageDot: { width: 22, height: 22, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 12, flexShrink: 0 },
  stageName: { fontWeight: 600, color: '#e6e6ea', minWidth: 140 },
  stageDetail: { color: '#8a8a96', fontSize: 12, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  stageElapsed: { color: '#7e7eff', fontSize: 11, fontFamily: 'ui-monospace, monospace' },
  stageChevron: { color: '#8a8a96', fontSize: 14 },
  stageBody: { padding: 14, borderTop: '1px solid #1f1f28', background: '#0f0f15' },

  empty: { color: '#666', fontSize: 13, fontStyle: 'italic' },
  assetExplain: { color: '#a8a8b0', fontSize: 12, lineHeight: 1.5, marginTop: 0, marginBottom: 14, padding: 10, background: '#0b0b0f', borderRadius: 4, border: '1px solid #1f1f28' },
  subhead: { color: '#7e7eff', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '12px 0 6px' },
  assetGrid: { display: 'flex', flexWrap: 'wrap', gap: 10 },
  assetCard: { background: '#15151c', borderRadius: 6, padding: 6, margin: 0, width: 130, display: 'flex', flexDirection: 'column', gap: 4 },
  assetImg: { width: '100%', height: 130, objectFit: 'cover', borderRadius: 4, background: 'black' },
  assetCaption: { fontSize: 11, color: '#ffd24a', textAlign: 'center', wordBreak: 'break-word' },
  assetPair: { background: '#15151c', borderRadius: 6, padding: 8, margin: 0, display: 'flex', flexDirection: 'column', gap: 6 },
  pairRow: { display: 'flex', alignItems: 'center', gap: 6 },
  pairImg: { width: 110, height: 130, objectFit: 'cover', borderRadius: 4, background: 'black' },
  pairArrow: { color: '#7e7eff', fontSize: 18, fontWeight: 700 },

  castingList: { display: 'flex', flexDirection: 'column', gap: 6 },
  castRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', background: '#15151c', borderRadius: 4, border: '1px solid #1f1f28' },
  castSceneNum: { fontFamily: 'ui-monospace, monospace', color: '#fa3e3e', fontWeight: 700, width: 36, fontSize: 12 },
  castTitle: { color: '#e6e6ea', fontWeight: 500, fontSize: 13 },
  castMeta: { display: 'flex', gap: 12, fontSize: 11, color: '#8a8a96', marginTop: 2 },
  castThumbs: { display: 'flex', gap: 6 },
  castThumb: { width: 40, height: 40, borderRadius: 4, objectFit: 'cover', background: 'black', border: '1px solid #2a2a36' },

  scriptHeader: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 },
  scriptTitle: { color: '#e6e6ea', fontWeight: 600, fontSize: 15 },
  outroChip: { background: '#3a2828', color: '#ff9090', padding: '2px 8px', borderRadius: 3, fontSize: 11 },
  scriptList: { listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 8 },
  scriptScene: { background: '#15151c', borderRadius: 4, padding: '10px 12px', border: '1px solid #1f1f28' },
  scriptSceneHead: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 },
  scriptSceneNum: { color: '#fa3e3e', fontFamily: 'ui-monospace, monospace', fontSize: 11, fontWeight: 700 },
  scriptSceneTitle: { color: '#e6e6ea', fontWeight: 500, fontSize: 13 },
  scriptBadge: { background: '#1a1a22', color: '#c5c5cf', padding: '2px 6px', borderRadius: 3, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' },
  scriptDesc: { color: '#a8a8b0', fontSize: 12, lineHeight: 1.4, marginTop: 4 },
  scriptLine: { color: '#e6e6ea', fontSize: 13, fontStyle: 'italic', marginTop: 6, padding: '6px 10px', background: '#0b0b0f', borderRadius: 3, borderLeft: '2px solid #ffd24a' },
  scriptAudio: { color: '#7e7eff', fontSize: 11, marginTop: 6 },

  directionScene: { background: '#15151c', borderRadius: 4, padding: '8px 10px', marginBottom: 6, border: '1px solid #1f1f28' },
  directionSummary: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: '#e6e6ea', listStyle: 'none' },
  directionPromptLen: { marginLeft: 'auto', color: '#7e7eff', fontSize: 10, fontFamily: 'ui-monospace, monospace' },
  directionPrompt: { background: '#0b0b0f', color: '#a8a8b0', padding: 12, borderRadius: 4, fontSize: 11, lineHeight: 1.45, fontFamily: 'ui-monospace, SFMono-Regular, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 360, overflowY: 'auto', marginTop: 8 },

  clipGrid: { display: 'flex', flexWrap: 'wrap', gap: 12 },
  clipTile: { background: '#15151c', borderRadius: 6, padding: 8, border: '1px solid #1f1f28', width: 220, display: 'flex', flexDirection: 'column', gap: 4 },
  clipHead: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#8a8a96', fontFamily: 'ui-monospace, monospace' },
  clipDot: { width: 8, height: 8, borderRadius: '50%', marginLeft: 'auto' },
  clipTitle: { fontSize: 12, color: '#e6e6ea', fontWeight: 500, lineHeight: 1.3 },
  clipVideo: { width: '100%', height: 240, objectFit: 'cover', borderRadius: 4, background: 'black' },
  clipPending: { textAlign: 'center', color: '#666', fontSize: 11, padding: '20px 0', height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' },
  clipErr: { color: '#ff8a8a', fontSize: 10, lineHeight: 1.3, wordBreak: 'break-word' },

  logPanel: { background: '#0b0b0f', border: '1px solid #1f1f28', borderRadius: 6, padding: 12, fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: 11, lineHeight: 1.5, maxHeight: 300, overflowY: 'auto' },
  logLine: { display: 'flex', gap: 8, alignItems: 'flex-start' },
  logTime: { color: '#555', flexShrink: 0 },
  logStageTag: { color: '#7e7eff', flexShrink: 0 },
}
