import { useMidi } from './midi/MidiProvider'

export function MidiMonitor() {
  const { status, error, inputs, recent, latestByKey, mappings, removeMapping } = useMidi()

  const latestRows = Array.from(latestByKey.values()).sort((a, b) =>
    a.device === b.device
      ? a.channel - b.channel || a.data1 - b.data1
      : a.device.localeCompare(b.device),
  )

  return (
    <div className="midi-monitor">
      <h2>MIDI Monitor</h2>
      <div className="midi-status">
        status: <strong>{status}</strong>
        {error && <span className="error"> — {error}</span>}
      </div>
      <div className="midi-inputs">
        inputs: {inputs.length === 0 ? 'none detected' : inputs.join(', ')}
      </div>

      <h3>Active mappings</h3>
      <table className="midi-table">
        <thead>
          <tr>
            <th>Parameter</th>
            <th>Device</th>
            <th>Ch</th>
            <th>Type</th>
            <th>#</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {mappings.length === 0 && (
            <tr>
              <td colSpan={6} className="empty">no mappings yet — click "+ New MIDI Connection"</td>
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
                <button onClick={() => removeMapping(m.controlId)}>remove</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

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
              <td colSpan={5} className="empty">move a knob/key to see messages…</td>
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

      <h3>Recent events (last 200)</h3>
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
          {recent.map((r, i) => (
            <tr key={i}>
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
  )
}
