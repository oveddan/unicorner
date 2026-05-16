import { useEffect, useRef, useState } from 'react'
import type { SetMessage } from '../types'

export type WSStatus = 'connecting' | 'open' | 'closed'

const WS_PORT = 9980

function wsUrl(): string {
  const host = window.location.hostname || '127.0.0.1'
  return `ws://${host}:${WS_PORT}`
}

export function useTDSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<WSStatus>('connecting')

  useEffect(() => {
    let cancelled = false
    let retryTimer: number | undefined

    const connect = () => {
      if (cancelled) return
      const url = wsUrl()
      setStatus('connecting')
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (cancelled) return
        setStatus('open')
        console.log(`[ws] open ${url}`)
      }
      ws.onclose = () => {
        if (cancelled) return
        setStatus('closed')
        console.log('[ws] closed; retrying in 2s')
        retryTimer = window.setTimeout(connect, 2000)
      }
      ws.onerror = (e) => {
        console.warn('[ws] error', e)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) window.clearTimeout(retryTimer)
      wsRef.current?.close()
    }
  }, [])

  const send = (msg: SetMessage) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg))
    } else {
      console.log('[ws] not open, dropping', msg)
    }
  }

  return { status, send }
}
