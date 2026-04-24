import { useEffect, useState } from 'react'
import { doc, onSnapshot } from 'firebase/firestore'
import { db, firestoreEnabled } from '../firebase'
import type { Segment } from '../types'
import { fetchSegment } from '../api'

/** Subscribe to a segment doc. Uses Firestore onSnapshot when configured,
 * otherwise polls the backend every second. */
export function useSegmentStatus(segmentId: string | null): Segment | null {
  const [segment, setSegment] = useState<Segment | null>(null)

  useEffect(() => {
    if (!segmentId) {
      setSegment(null)
      return
    }

    if (firestoreEnabled && db) {
      const unsub = onSnapshot(doc(db, 'segments', segmentId), (snap) => {
        if (snap.exists()) {
          setSegment(snap.data() as Segment)
        }
      })
      return unsub
    }

    // Polling fallback
    let cancelled = false
    const tick = async () => {
      try {
        const { segment } = await fetchSegment(segmentId)
        if (!cancelled) setSegment(segment)
      } catch {
        // transient — try again
      }
    }
    tick()
    const interval = window.setInterval(tick, 1000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [segmentId])

  return segment
}
