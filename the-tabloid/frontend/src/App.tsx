import { useCallback, useEffect, useState } from 'react'
import { ChannelSelect } from './pages/ChannelSelect'
import { StoryPicker } from './pages/StoryPicker'
import { PersonaSelect } from './pages/PersonaSelect'
import { Debate } from './pages/Debate'
import { Player } from './pages/Player'
import { History } from './pages/History'
import { Drama } from './pages/Drama'
import { DramaProgress } from './pages/DramaProgress'
import { fetchChannels, startSegment } from './api'
import type {
  ChannelId,
  ChannelInfo,
  Persona,
  Segment,
  SegmentMode,
  StoryCandidate,
} from './types'

type View =
  | { name: 'channel_select' }
  | { name: 'history' }
  | { name: 'story_picker'; channel: ChannelInfo }
  | { name: 'persona_select'; channel: ChannelInfo; story: StoryCandidate | null }
  | { name: 'debate'; channel: ChannelInfo; segmentId: string }
  | { name: 'player'; channel: ChannelInfo; segment: Segment }
  | { name: 'drama' }
  | { name: 'drama_progress'; segmentId: string }

export default function App() {
  const [view, setView] = useState<View>({ name: 'channel_select' })
  // Default to sample — video (tabloid) burns paid tokens on every run,
  // so we make the user opt in explicitly.
  const [mode, setMode] = useState<SegmentMode>('sample')

  const goHome = useCallback(() => setView({ name: 'channel_select' }), [])

  // Hash-based deep link to /drama so the user can land directly on the
  // short-drama UI without going through ChannelSelect. Anything else
  // routes through the normal panel_debate flow.
  useEffect(() => {
    const apply = () => {
      if (window.location.hash === '#/drama') setView({ name: 'drama' })
    }
    apply()
    window.addEventListener('hashchange', apply)
    return () => window.removeEventListener('hashchange', apply)
  }, [])

  switch (view.name) {
    case 'channel_select':
      return (
        <ChannelSelect
          mode={mode}
          onModeChange={setMode}
          onPick={(ch) => setView({ name: 'story_picker', channel: ch })}
          onShowHistory={() => setView({ name: 'history' })}
          onShowDrama={() => {
            window.location.hash = '#/drama'
            setView({ name: 'drama' })
          }}
        />
      )

    case 'drama':
      return (
        <Drama
          onBack={() => {
            window.location.hash = ''
            goHome()
          }}
          onStart={(segmentId) =>
            setView({ name: 'drama_progress', segmentId })
          }
        />
      )

    case 'drama_progress':
      return (
        <DramaProgress
          segmentId={view.segmentId}
          onBack={() => setView({ name: 'drama' })}
        />
      )

    case 'history':
      return (
        <History
          onBack={goHome}
          onOpen={async (row) => {
            // Look up the full ChannelInfo for the row's channel id, then
            // jump straight to the Player with the existing segment loaded.
            try {
              const channels = await fetchChannels()
              const ch = channels[row.channel as ChannelId]
              if (!ch) return
              setView({ name: 'player', channel: ch, segment: row })
            } catch {
              // ignore — user can navigate back if anything misfires
            }
          }}
        />
      )

    case 'story_picker':
      return (
        <StoryPicker
          channel={view.channel}
          onBack={goHome}
          onPick={(story) =>
            setView({ name: 'persona_select', channel: view.channel, story })
          }
          onAutoPick={() =>
            setView({ name: 'persona_select', channel: view.channel, story: null })
          }
        />
      )

    case 'persona_select':
      return (
        <PersonaSelect
          channel={view.channel}
          story={view.story}
          onBack={() => setView({ name: 'story_picker', channel: view.channel })}
          onStart={async (panel: Persona[]) => {
            // PersonaSelect ran the casting LLM call before this view
            // rendered, so `panel` is already the story-relevant cast
            // (or the user-swapped variant of it). Send it as-is so the
            // pipeline doesn't re-cast.
            const { segment_id } = await startSegment(
              view.channel.id,
              panel,
              mode,
              view.story,
            )
            setView({ name: 'debate', channel: view.channel, segmentId: segment_id })
          }}
        />
      )

    case 'debate':
      return (
        <Debate
          channel={view.channel}
          segmentId={view.segmentId}
          onBack={goHome}
          onReady={(segment) =>
            setView({ name: 'player', channel: view.channel, segment })
          }
        />
      )

    case 'player':
      return (
        <Player
          channel={view.channel}
          segment={view.segment}
          onBack={goHome}
          onNewChannel={goHome}
          onNewStory={() =>
            setView({ name: 'story_picker', channel: view.channel })
          }
        />
      )
  }
}
