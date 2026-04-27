import { useCallback, useState } from 'react'
import { ChannelSelect } from './pages/ChannelSelect'
import { StoryPicker } from './pages/StoryPicker'
import { PersonaSelect } from './pages/PersonaSelect'
import { Debate } from './pages/Debate'
import { Player } from './pages/Player'
import { History } from './pages/History'
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

export default function App() {
  const [view, setView] = useState<View>({ name: 'channel_select' })
  // Default to sample — video (tabloid) burns paid tokens on every run,
  // so we make the user opt in explicitly.
  const [mode, setMode] = useState<SegmentMode>('sample')

  const goHome = useCallback(() => setView({ name: 'channel_select' }), [])

  switch (view.name) {
    case 'channel_select':
      return (
        <ChannelSelect
          mode={mode}
          onModeChange={setMode}
          onPick={(ch) => setView({ name: 'story_picker', channel: ch })}
          onShowHistory={() => setView({ name: 'history' })}
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
          onBack={() => setView({ name: 'story_picker', channel: view.channel })}
          onStart={async (panel: Persona[]) => {
            // If the user didn't actually swap anyone, send personas=null so
            // the backend's casting agent picks story-relevant guests fresh.
            // Sending the unchanged default_panel locks us into the static
            // pool, which produced the Tamil-NA-personas-on-Delhi-stories
            // mismatch.
            const baseline = view.channel.default_panel
            const unchanged =
              panel.length === baseline.length &&
              panel.every((p, i) => p.id === baseline[i]?.id)
            const { segment_id } = await startSegment(
              view.channel.id,
              unchanged ? undefined : panel,
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
