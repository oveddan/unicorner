import { useEffect, useState } from 'react'
import type { SetMessage } from '../types'
import { useSend } from '../transport/context'
import { useMidi } from '../midi/MidiProvider'
import { EditableLabel } from './EditableLabel'

type Props = {
  id: string
  label: string
  path: string
  onLabelChange: (next: string) => void
}

export function ManualSlider({ id, label, path, onLabelChange }: Props) {
  const [norm, setNorm] = useState(0)
  const send = useSend()
  const { normByControl, mappings } = useMidi()
  const midiNorm = normByControl.get(id)
  const activeNorm = midiNorm ?? norm
  const mapping = mappings.find((m) => m.controlId === id)

  useEffect(() => {
    if (midiNorm == null) return
    send({ type: 'set', path, value: midiNorm } satisfies SetMessage)
  }, [midiNorm, path, send])

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const n = Number(e.target.value)
    setNorm(n)
    send({ type: 'set', path, value: n } satisfies SetMessage)
  }

  return (
    <div className="widget">
      <div>
        <div className="widget-label">
          <EditableLabel value={label} onChange={onLabelChange} />
          {mapping && (
            <span className="widget-midi">
              {mapping.sig.type} #{mapping.sig.data1} ch{mapping.sig.channel}
            </span>
          )}
        </div>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.001}
        value={activeNorm}
        onChange={onChange}
      />
      <div className="widget-value">{activeNorm.toFixed(4)}</div>
    </div>
  )
}
