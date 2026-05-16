import { useEffect, useState } from 'react'
import type { Control, ControllerSpec } from './types'
import { Knob } from './widgets/Knob'
import { Toggle } from './widgets/Toggle'
import { Unsupported } from './widgets/Unsupported'
import { useTDSocket } from './transport/ws'
import { SendContext } from './transport/context'
import { MidiMonitor } from './MidiMonitor'
import { MidiLearnModal } from './MidiLearnModal'

function renderControl(control: Control) {
  switch (control.type) {
    case 'knob':
      return <Knob key={control.id} control={control} />
    case 'toggle':
      return <Toggle key={control.id} control={control} />
    default:
      return <Unsupported key={control.id} control={control} />
  }
}

function ControlsGrid({ spec }: { spec: ControllerSpec }) {
  const byId = new Map(spec.controls.map((c) => [c.id, c]))

  if (spec.layout && spec.layout.length > 0) {
    return (
      <div className="rows">
        {spec.layout.map((row, i) => (
          <div className="row" key={i}>
            {row.map((id) => {
              const c = byId.get(id)
              return c ? renderControl(c) : null
            })}
          </div>
        ))}
      </div>
    )
  }

  return <div className="grid">{spec.controls.map(renderControl)}</div>
}

export default function App() {
  const [spec, setSpec] = useState<ControllerSpec | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [learnOpen, setLearnOpen] = useState(false)
  const { status, send } = useTDSocket()

  useEffect(() => {
    fetch('/specs/a.json')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data: ControllerSpec) => setSpec(data))
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <SendContext value={send}>
      <div className="app">
        <header>
          <h1>Unicorner Controller</h1>
          <div className="scene">scene: {spec?.scene_id ?? '…'}</div>
          <div className={`wsbadge wsbadge-${status}`}>td: {status}</div>
          <button className="new-midi-btn" onClick={() => setLearnOpen(true)} disabled={!spec}>
            + New MIDI Connection
          </button>
        </header>
        {error && <div className="error">Failed to load spec: {error}</div>}
        {!error && !spec && <div className="loading">Loading…</div>}
        {spec && <ControlsGrid spec={spec} />}
        <MidiMonitor />
        {learnOpen && spec && <MidiLearnModal spec={spec} onClose={() => setLearnOpen(false)} />}
      </div>
    </SendContext>
  )
}
