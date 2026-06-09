import { useEffect, useState } from 'react'
import { streamUrl } from './api'

export type LogLine = { seq: number; stream: string; line: string }
export type Done = { status: string; exit?: number; [k: string]: unknown }

// Subscribe to a run's SSE stream: live log lines + a terminal `done` event.
// The server replays persisted lines first, so late subscribers see full history.
export function useRunStream(runId: string | null) {
  const [lines, setLines] = useState<LogLine[]>([])
  const [done, setDone] = useState<Done | null>(null)

  useEffect(() => {
    setLines([])
    setDone(null)
    if (!runId) return
    const es = new EventSource(streamUrl(runId))
    es.addEventListener('log', (e: MessageEvent) => {
      const d = JSON.parse(e.data) as LogLine
      setLines((prev) => [...prev, d])
    })
    es.addEventListener('done', (e: MessageEvent) => {
      setDone(JSON.parse(e.data) as Done)
      es.close()
    })
    es.onerror = () => { /* browser auto-reconnects with Last-Event-ID */ }
    return () => es.close()
  }, [runId])

  return { lines, done }
}
