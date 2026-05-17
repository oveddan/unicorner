import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/* eslint-disable react-refresh/only-export-components */

export type MidiEvent = {
  time: number
  device: string
  channel: number
  type: string
  data1: number
  data2: number
  value: number
  norm: number
}

export type MidiSignature = {
  device: string
  channel: number
  type: string
  data1: number
}

export type Mapping = { controlId: string; sig: MidiSignature }

type Ctx = {
  status: 'idle' | 'requesting' | 'ready' | 'error'
  error: string | null
  inputs: string[]
  lastEvent: MidiEvent | null
  recent: MidiEvent[]
  latestByKey: Map<string, MidiEvent>
  mappings: Mapping[]
  addMapping: (controlId: string, sig: MidiSignature) => void
  removeMapping: (controlId: string) => void
  normByControl: Map<string, number>
}

const MidiCtx = createContext<Ctx | null>(null)

export function useMidi() {
  const c = useContext(MidiCtx)
  if (!c) throw new Error('useMidi must be used inside <MidiProvider>')
  return c
}

const STATUS_TYPES: Record<number, string> = {
  0x80: 'Note Off',
  0x90: 'Note On',
  0xa0: 'Aftertouch',
  0xb0: 'CC',
  0xc0: 'Program',
  0xd0: 'Channel Pressure',
  0xe0: 'Pitch Bend',
}

const MAPPINGS_KEY = 'unicorner.midi.mappings.v1'

export function sigKey(s: MidiSignature) {
  return `${s.device}|${s.channel}|${s.type}|${s.data1}`
}

export function MidiProvider({ children }: { children: ReactNode }) {
  const hasMidiAccess =
    typeof navigator !== 'undefined' && typeof navigator.requestMIDIAccess === 'function'
  const [status, setStatus] = useState<Ctx['status']>(hasMidiAccess ? 'requesting' : 'error')
  const [error, setError] = useState<string | null>(
    hasMidiAccess ? null : 'Web MIDI API not supported. Use Chrome or Edge.',
  )
  const [inputs, setInputs] = useState<string[]>([])
  const [lastEvent, setLastEvent] = useState<MidiEvent | null>(null)
  const [recent, setRecent] = useState<MidiEvent[]>([])
  const [latestByKey, setLatestByKey] = useState<Map<string, MidiEvent>>(new Map())
  const [mappings, setMappings] = useState<Mapping[]>(() => {
    try {
      const raw = localStorage.getItem(MAPPINGS_KEY)
      if (!raw) return []
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? (parsed as Mapping[]) : []
    } catch {
      return []
    }
  })
  const [normByControl, setNormByControl] = useState<Map<string, number>>(new Map())
  const mappingsRef = useRef(mappings)

  useEffect(() => {
    mappingsRef.current = mappings
  }, [mappings])

  useEffect(() => {
    try {
      localStorage.setItem(MAPPINGS_KEY, JSON.stringify(mappings))
    } catch {
      // Ignore disabled storage or quota issues; live MIDI still works.
    }
  }, [mappings])

  useEffect(() => {
    if (!navigator.requestMIDIAccess) {
      return
    }

    navigator
      .requestMIDIAccess({ sysex: false })
      .then((access) => {
        const refreshInputs = () => {
          const names: string[] = []
          access.inputs.forEach((i) => names.push(i.name ?? i.id))
          setInputs(names)
        }

        const handle = (input: MIDIInput) => (event: MIDIMessageEvent) => {
          const data = event.data
          if (!data) return
          const sb = data[0]
          if (sb >= 0xf8) return

          const d1 = data[1] ?? 0
          const d2 = data[2] ?? 0
          const high = sb & 0xf0
          const channel = (sb & 0x0f) + 1
          const type = STATUS_TYPES[high] ?? `0x${high.toString(16)}`
          let rawValue = d2
          let norm = d2 / 127

          if (high === 0xe0) {
            const v = ((d2 << 7) | d1) - 8192
            rawValue = v
            norm = (v + 8192) / 16383
          } else if (high === 0xc0 || high === 0xd0) {
            rawValue = d1
            norm = d1 / 127
          } else if (high === 0x80) {
            norm = 0
          }

          const deviceName = input.name ?? input.id
          const ev: MidiEvent = {
            time: performance.now(),
            device: deviceName,
            channel,
            type,
            data1: d1,
            data2: d2,
            value: rawValue,
            norm,
          }
          setLastEvent(ev)
          setRecent((p) => [ev, ...p].slice(0, 200))

          const key = sigKey({ device: deviceName, channel, type, data1: d1 })
          setLatestByKey((p) => {
            const n = new Map(p)
            n.set(key, ev)
            return n
          })

          // Treat Note On and Note Off as two halves of the same logical mapping
          // so a Note On mapping also receives the Note Off as a release (norm=0).
          const altKey =
            type === 'Note On'
              ? sigKey({ device: deviceName, channel, type: 'Note Off', data1: d1 })
              : type === 'Note Off'
                ? sigKey({ device: deviceName, channel, type: 'Note On', data1: d1 })
                : null

          for (const m of mappingsRef.current) {
            const mKey = sigKey(m.sig)
            if (mKey === key || (altKey && mKey === altKey)) {
              setNormByControl((p) => {
                const n = new Map(p)
                n.set(m.controlId, ev.norm)
                return n
              })
            }
          }
        }

        const attach = () => {
          access.inputs.forEach((input) => {
            input.onmidimessage = handle(input)
          })
          refreshInputs()
        }

        attach()
        access.onstatechange = attach
        setStatus('ready')
      })
      .catch((e) => {
        setStatus('error')
        setError(String(e))
      })
  }, [])

  const addMapping = (controlId: string, sig: MidiSignature) =>
    setMappings((p) => [...p.filter((m) => m.controlId !== controlId), { controlId, sig }])

  const removeMapping = (controlId: string) =>
    setMappings((p) => p.filter((m) => m.controlId !== controlId))

  const value: Ctx = {
    status,
    error,
    inputs,
    lastEvent,
    recent,
    latestByKey,
    mappings,
    addMapping,
    removeMapping,
    normByControl,
  }

  return <MidiCtx value={value}>{children}</MidiCtx>
}
