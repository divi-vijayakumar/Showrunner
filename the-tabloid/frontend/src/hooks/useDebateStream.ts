import { useEffect, useState } from 'react'
import { collection, onSnapshot, orderBy, query } from 'firebase/firestore'
import { db, firestoreEnabled } from '../firebase'
import type { DebateMessage } from '../types'
import { fetchSegment } from '../api'

/** Stream debate messages in `seq` order. Firestore subscription when configured,
 * polling fallback otherwise — de-dupes new messages on each poll. */
export function useDebateStream(segmentId: string | null): DebateMessage[] {
  const [messages, setMessages] = useState<DebateMessage[]>([])

  useEffect(() => {
    setMessages([])
    if (!segmentId) return

    if (firestoreEnabled && db) {
      const q = query(
        collection(db, `segments/${segmentId}/messages`),
        orderBy('seq'),
      )
      const unsub = onSnapshot(q, (snap) => {
        const all = snap.docs.map((d) => d.data() as DebateMessage)
        setMessages(all)
      })
      return unsub
    }

    // Polling fallback
    let cancelled = false
    const tick = async () => {
      try {
        const { messages } = await fetchSegment(segmentId)
        if (!cancelled) {
          setMessages([...messages].sort((a, b) => a.seq - b.seq))
        }
      } catch {
        // ignore transient
      }
    }
    tick()
    const interval = window.setInterval(tick, 1000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [segmentId])

  return messages
}
