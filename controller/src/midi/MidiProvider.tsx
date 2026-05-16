import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

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

export function sigKey(s: MidiSignature) {
  return `${s.device}|${s.channel}|${s.type}|${s.data1}`
}

export function MidiProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Ctx['status']>('idle')
  const [error, setError] = useState<string | null>(null)
  const [inputs, setInputs] = useState<string[]>([])
  const [lastEvent, setLastEvent] = useState<MidiEvent | null>(null)
  const [recent, setRecent] = useState<MidiEvent[]>([])
  const [latestByKey, setLatestByKey] = useState<Map<string, MidiEvent>>(new Map())
  const [mappings, setMappings] = useState<Mapping[]>([])
  const [normByControl, setNormByControl] = useState<Map<string, number>>(new Map())
  const mappingsRef = useRef(mappings)
  mappingsRef.current = mappings

  useEffect(() => {
    if (!navigator.requestMIDIAccess) {
      setStatus('error')
      setError('Web MIDI API not supported. Use Chrome or Edge.')
      return
    }
    setStatus('requesting')
    navigator
      .requestMIDIAccess({ sysex: false })
      .then((access) => {
        const refreshInputs = () => {
          const names: string[] = []
          access.inputs.forEach((i) => names.push(i.name ?? i.id))
          setInputs(names)
        }
        const handle = (input: MIDIInput) => (event: MIDIMessageEvent) => {
          const data = event.data as Uint8Array
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
          for (const m of mappingsRef.current) {
            if (sigKey(m.sig) === key) {
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
