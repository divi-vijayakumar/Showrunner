import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type DramaAsset,
  fetchRecentDramaAssets,
  generateDrama,
  uploadDramaAsset,
} from '../api'

interface Props {
  onBack: () => void
  onStart: (segmentId: string) => void
}

const SAMPLE_BEAT_SHEET = `Scene 1:
may 4, 2026 morning.
vijay (sony pixarized version of vijay) entering the steps of tamilnadu parliament.
 -> background voiceover "C. Joseph Vijay enum naan" (language Tamil)

pan to crowd cheering "thalaiva thalaiva..."
on-screen text: CM 2026

Scene 2: opinion polls asking tamil natives.
45 year old man -> Tamil ("he can't win"), 55 year old ("he doesn't have political history"),
another ("he is just an actor"), another ("tharkuri").

Scene 3: young women -> "he is my brother". young 19 year old -> "he is my thalaivar".
10 year old -> "vijay mamaku tvk ku vote podunga". 60 year grandmother -> "vijay ku whistle ku vote potan".

play motivating music.
end with -> "I'm waiting" (vijay's dialog) -> CM 2026.`

export function Drama({ onBack, onStart }: Props) {
  const [beatSheet, setBeatSheet] = useState(SAMPLE_BEAT_SHEET)
  const [title, setTitle] = useState('CM 2026')
  // Tamil was removed from the UI: the audio moderator rejected most Tamil
  // dialog generations, and Seedance's Tamil pronunciation is unreliable.
  // English-only until we have a working TTS path for Tamil.
  const language: 'english' = 'english'
  const [characters, setCharacters] = useState<DramaAsset[]>([])
  const [sets, setSets] = useState<DramaAsset[]>([])
  const [busy, setBusy] = useState<'idle' | 'uploading' | 'generating'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [resumeNote, setResumeNote] = useState<string | null>(null)

  // On mount, ask the backend for any previously-uploaded assets so the page
  // rehydrates after a navigation/refresh — no need to re-upload + re-label.
  // Sidecar JSON written on each upload preserves label/kind/description.
  useEffect(() => {
    let cancelled = false
    fetchRecentDramaAssets(60)
      .then(({ assets }) => {
        if (cancelled || assets.length === 0) return
        // Only rehydrate assets that carry sidecar metadata (label/kind set
        // by the user). Unlabelled ones from earlier sessions are skipped —
        // bringing them back as `asset_xxx` is more confusing than helpful.
        const labelled = assets.filter((a) => a.has_label)
        if (labelled.length === 0) return
        const chars = labelled.filter((a) => a.kind === 'character')
        const setsList = labelled.filter((a) => a.kind === 'set')
        setCharacters(chars)
        setSets(setsList)
        setResumeNote(
          `Resumed ${chars.length} character${chars.length === 1 ? '' : 's'} and ${setsList.length} set${setsList.length === 1 ? '' : 's'} from previous session. Edit or remove tiles below, or click Generate.`,
        )
      })
      .catch(() => { /* fine — first-time user has nothing to resume */ })
    return () => { cancelled = true }
  }, [])

  const onUpload = useCallback(
    async (file: File, kind: 'character' | 'set') => {
      const label = window.prompt(
        kind === 'character'
          ? 'Character label (e.g. "vijay" or "young woman 19")'
          : 'Set label (e.g. "tn_parliament_steps")',
      )
      if (!label?.trim()) return
      const description = window.prompt(
        kind === 'character'
          ? 'Optional 1-line description (age, hair, wardrobe — helps Pixarify keep identity). Leave blank to skip.'
          : 'Optional 1-line description (location context). Leave blank to skip.',
        '',
      ) || ''
      try {
        setBusy('uploading')
        setError(null)
        const asset = await uploadDramaAsset(file, label.trim(), kind, description.trim())
        if (kind === 'character') setCharacters((cs) => [...cs, asset])
        else setSets((ss) => [...ss, asset])
      } catch (exc) {
        setError(`Upload failed: ${(exc as Error).message}`)
      } finally {
        setBusy('idle')
      }
    },
    [],
  )

  const onGenerate = useCallback(async () => {
    if (!beatSheet.trim()) {
      setError('Beat sheet is required')
      return
    }
    setError(null)
    setBusy('generating')
    try {
      const { segment_id } = await generateDrama({
        beat_sheet: beatSheet,
        title: title.trim() || 'Short Drama',
        default_language: language,
        characters: characters.map((c) => ({
          asset_id: c.asset_id, label: c.label, description: c.description,
        })),
        sets: sets.map((s) => ({
          asset_id: s.asset_id, label: s.label, description: s.description,
        })),
      })
      onStart(segment_id)
    } catch (exc) {
      setError(`Generate failed: ${(exc as Error).message}`)
      setBusy('idle')
    }
  }, [beatSheet, title, language, characters, sets, onStart])

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <button onClick={onBack} style={styles.backBtn}>← Back</button>
        <h1 style={styles.title}>Short Drama</h1>
        <span style={styles.subtitle}>
          Beat sheet → Pixar 2-min film with native English audio
        </span>
      </header>

      <main style={styles.main}>
        <section style={styles.section}>
          <label style={styles.label}>Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={styles.input}
            placeholder="CM 2026"
          />
        </section>

        <section style={styles.section}>
          <label style={styles.label}>Beat sheet</label>
          <textarea
            value={beatSheet}
            onChange={(e) => setBeatSheet(e.target.value)}
            style={styles.textarea}
            spellCheck={false}
          />
        </section>

        <section style={styles.section}>
          <label style={styles.label}>
            Character photos (Pixarified — preserves identity, swaps render style)
          </label>
          <AssetUploader
            kind="character"
            assets={characters}
            onUpload={onUpload}
            onRemove={(id) => setCharacters((cs) => cs.filter((c) => c.asset_id !== id))}
          />
        </section>

        <section style={styles.section}>
          <label style={styles.label}>
            Set photos (optional — writer also generates from descriptions)
          </label>
          <AssetUploader
            kind="set"
            assets={sets}
            onUpload={onUpload}
            onRemove={(id) => setSets((ss) => ss.filter((s) => s.asset_id !== id))}
          />
        </section>

        {resumeNote && (
          <div style={styles.resumeBanner}>
            <span>{resumeNote}</span>
            <button
              onClick={() => { setCharacters([]); setSets([]); setResumeNote(null) }}
              style={styles.clearBtn}
            >
              Clear all
            </button>
          </div>
        )}

        {error && <div style={styles.error}>{error}</div>}

        <div style={styles.actions}>
          <button
            onClick={onGenerate}
            disabled={busy !== 'idle'}
            style={{
              ...styles.generateBtn,
              opacity: busy === 'idle' ? 1 : 0.5,
              cursor: busy === 'idle' ? 'pointer' : 'wait',
            }}
          >
            {busy === 'generating' ? 'Generating…' : busy === 'uploading' ? 'Uploading…' : 'Generate Film'}
          </button>
          <span style={styles.hint}>
            ~{Math.max(60, Math.min(180, characters.length * 30 + 60))}s end-to-end with the 4-key BytePlus pool
          </span>
        </div>
      </main>
    </div>
  )
}

interface AssetUploaderProps {
  kind: 'character' | 'set'
  assets: DramaAsset[]
  onUpload: (file: File, kind: 'character' | 'set') => void
  onRemove: (id: string) => void
}

function AssetUploader({ kind, assets, onUpload, onRemove }: AssetUploaderProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [hover, setHover] = useState(false)

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setHover(false)
      const f = e.dataTransfer.files?.[0]
      if (f) onUpload(f, kind)
    },
    [onUpload, kind],
  )

  return (
    <>
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setHover(true) }}
        onDragLeave={() => setHover(false)}
        onDrop={onDrop}
        style={{
          ...styles.dropZone,
          background: hover ? '#1f2030' : '#15151c',
          borderColor: hover ? '#7e7eff' : '#2a2a36',
        }}
      >
        Drag-drop a {kind} photo here, or click to pick.
        <br />
        <span style={styles.dropHint}>
          jpg / png / webp · you'll be prompted for a label after upload
        </span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onUpload(f, kind)
          e.target.value = ''
        }}
      />
      {assets.length > 0 && (
        <div style={styles.tileRow}>
          {assets.map((a) => (
            <div key={a.asset_id} style={styles.tile}>
              <img src={a.url} alt={a.label} style={styles.thumb} />
              <div style={styles.tileLabel}>{a.label}</div>
              {a.description && <div style={styles.tileDesc}>{a.description}</div>}
              <button onClick={() => onRemove(a.asset_id)} style={styles.removeBtn}>
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    background: '#0b0b0f',
    color: '#e6e6ea',
    minHeight: '100vh',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
  },
  header: {
    padding: '20px 32px',
    borderBottom: '1px solid #1a1a22',
    display: 'flex',
    alignItems: 'center',
    gap: 16,
  },
  backBtn: {
    background: 'transparent',
    border: '1px solid #2a2a36',
    color: '#c5c5cf',
    padding: '6px 12px',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
  title: { margin: 0, fontSize: 20, color: '#fa3e3e', letterSpacing: '0.04em', textTransform: 'uppercase' },
  subtitle: { color: '#8a8a96', fontSize: 13, marginLeft: 'auto' },
  main: { maxWidth: 880, margin: '0 auto', padding: '24px 32px 48px', display: 'flex', flexDirection: 'column', gap: 22 },
  section: { display: 'flex', flexDirection: 'column', gap: 8 },
  label: { fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#8a8a96' },
  input: {
    background: '#15151c', color: '#e6e6ea', border: '1px solid #2a2a36',
    borderRadius: 4, padding: '8px 10px', fontSize: 14,
  },
  textarea: {
    background: '#15151c', color: '#e6e6ea', border: '1px solid #2a2a36',
    borderRadius: 4, padding: 12, fontSize: 13, fontFamily: 'ui-monospace, SFMono-Regular, monospace',
    minHeight: 240, resize: 'vertical', lineHeight: 1.5,
  },
  langRow: { display: 'flex', gap: 18 },
  langLabel: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, color: '#c5c5cf', cursor: 'pointer' },
  dropZone: {
    border: '1.5px dashed', borderRadius: 6, padding: 18, textAlign: 'center',
    fontSize: 13, color: '#c5c5cf', cursor: 'pointer', transition: 'all 0.15s',
  },
  dropHint: { fontSize: 11, color: '#8a8a96' },
  tileRow: { display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 10 },
  tile: {
    background: '#15151c', border: '1px solid #2a2a36', borderRadius: 6,
    padding: 8, width: 150, display: 'flex', flexDirection: 'column', gap: 4,
  },
  thumb: { width: '100%', height: 110, objectFit: 'cover', borderRadius: 4, background: '#000' },
  tileLabel: { fontSize: 12, color: '#ffd24a', fontWeight: 600, wordBreak: 'break-word' },
  tileDesc: { fontSize: 10, color: '#8a8a96', lineHeight: 1.3 },
  removeBtn: {
    background: '#2a1a1a', color: '#ff8a8a', border: '1px solid #3a2020',
    fontSize: 11, padding: '3px 6px', borderRadius: 3, cursor: 'pointer', marginTop: 4,
  },
  error: {
    background: '#2a1a1a', border: '1px solid #5a2828', color: '#ff8a8a',
    padding: '10px 14px', borderRadius: 4, fontSize: 13,
  },
  resumeBanner: {
    background: '#1a2030', border: '1px solid #2a3050', color: '#a8b8e8',
    padding: '10px 14px', borderRadius: 4, fontSize: 13,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
  },
  clearBtn: {
    background: 'transparent', border: '1px solid #5a4a4a', color: '#ff9090',
    padding: '4px 10px', borderRadius: 3, fontSize: 11, cursor: 'pointer',
    flexShrink: 0,
  },
  actions: { display: 'flex', alignItems: 'center', gap: 14, marginTop: 8 },
  generateBtn: {
    background: '#fa3e3e', color: 'white', border: 0, padding: '10px 22px',
    borderRadius: 4, fontSize: 14, fontWeight: 600, letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
  hint: { color: '#8a8a96', fontSize: 12 },
}
