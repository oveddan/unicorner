import { useState } from 'react'
import { useMidi } from './midi/MidiProvider'

export function MidiMonitor() {
  const { status, error, inputs, recent, latestByKey, mappings, removeMapping } = useMidi()
  const [expanded, setExpanded] = useState(false)
  const eventsShown = expanded ? recent.slice(0, 100) : recent.slice(0, 2)

  const latestRows = Array.from(latestByKey.values()).sort((a, b) =>
    a.device === b.device
      ? a.channel - b.channel || a.data1 - b.data1
      : a.device.localeCompare(b.device),
  )

  return (
    <div className="midi-monitor">
      <div className="midi-monitor-header">
        <div>
          <p className="eyebrow">MIDI</p>
          <h2>MIDI Monitor</h2>
        </div>
        <div className={`midi-state midi-state-${status}`}>
          <span>Status</span>
          <strong>{status}</strong>
        </div>
      </div>

      {error && <div className="midi-error">{error}</div>}
      <div className="midi-inputs">
        <span>Inputs</span>
        <strong>{inputs.length === 0 ? 'none detected' : inputs.join(', ')}</strong>
      </div>

      <div className="midi-table-group">
        <h3>Active mappings</h3>
        <table className="midi-table">
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Device</th>
              <th>Ch</th>
              <th>Type</th>
              <th>#</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {mappings.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  no mappings yet
                </td>
              </tr>
            )}
            {mappings.map((m) => (
              <tr key={m.controlId}>
                <td>{m.controlId}</td>
                <td>{m.sig.device}</td>
                <td>{m.sig.channel}</td>
                <td>{m.sig.type}</td>
                <td>{m.sig.data1}</td>
                <td>
                  <button className="table-action" type="button" onClick={() => removeMapping(m.controlId)}>
                    remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="midi-table-group">
        <h3>Current control values</h3>
        <table className="midi-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>Ch</th>
              <th>Type</th>
              <th>#</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {latestRows.length === 0 && (
              <tr>
                <td colSpan={5} className="empty">
                  move a knob/key to see messages
                </td>
              </tr>
            )}
            {latestRows.map((r) => (
              <tr key={`${r.device}-${r.channel}-${r.type}-${r.data1}`}>
                <td>{r.device}</td>
                <td>{r.channel}</td>
                <td>{r.type}</td>
                <td>{r.data1}</td>
                <td>{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="midi-table-group">
        <h3>
          Recent events ({expanded ? 'last 100' : 'last 2'})
          <button
            className="expand-btn"
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? 'Collapse recent MIDI events' : 'Expand recent MIDI events'}
          >
            {expanded ? '-' : '+'}
          </button>
        </h3>
        <table className="midi-table midi-log">
          <thead>
            <tr>
              <th>t (ms)</th>
              <th>Device</th>
              <th>Ch</th>
              <th>Type</th>
              <th>d1</th>
              <th>d2</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {eventsShown.length === 0 && (
              <tr>
                <td colSpan={7} className="empty">
                  no recent MIDI events
                </td>
              </tr>
            )}
            {eventsShown.map((r, i) => (
              <tr key={`${r.time}-${i}`}>
                <td>{r.time.toFixed(0)}</td>
                <td>{r.device}</td>
                <td>{r.channel}</td>
                <td>{r.type}</td>
                <td>{r.data1}</td>
                <td>{r.data2}</td>
                <td>{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
