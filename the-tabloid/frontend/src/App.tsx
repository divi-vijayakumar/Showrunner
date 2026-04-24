import { useCallback, useState } from 'react'
import { ChannelSelect } from './pages/ChannelSelect'
import { PersonaSelect } from './pages/PersonaSelect'
import { Debate } from './pages/Debate'
import { Player } from './pages/Player'
import { startSegment } from './api'
import type { ChannelInfo, Persona, Segment, SegmentMode } from './types'

type View =
  | { name: 'channel_select' }
  | { name: 'persona_select'; channel: ChannelInfo }
  | { name: 'debate'; channel: ChannelInfo; segmentId: string }
  | { name: 'player'; channel: ChannelInfo; segment: Segment }

export default function App() {
  const [view, setView] = useState<View>({ name: 'channel_select' })
  // Default to podcast — video (tabloid) burns paid tokens on every run,
  // so we make the user opt in explicitly.
  const [mode, setMode] = useState<SegmentMode>('podcast')

  const goHome = useCallback(() => setView({ name: 'channel_select' }), [])

  switch (view.name) {
    case 'channel_select':
      return (
        <ChannelSelect
          mode={mode}
          onModeChange={setMode}
          onPick={(ch) => setView({ name: 'persona_select', channel: ch })}
        />
      )

    case 'persona_select':
      return (
        <PersonaSelect
          channel={view.channel}
          onBack={goHome}
          onStart={async (panel: Persona[]) => {
            const { segment_id } = await startSegment(view.channel.id, panel, mode)
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
            setView({ name: 'persona_select', channel: view.channel })
          }
        />
      )
  }
}
