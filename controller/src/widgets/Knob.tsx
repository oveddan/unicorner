import { useEffect, useState } from 'react'
import type { KnobControl, SetMessage } from '../types'
import { normToValue } from '../mapping'
import { useSend } from '../transport/context'
import { useMidi } from '../midi/MidiProvider'

type Props = { control: KnobControl }

export function Knob({ control }: Props) {
  const { bind, label } = control
  const min = bind.min ?? 0
  const max = bind.max ?? 1
  const curve = bind.curve ?? 'linear'
  const [norm, setNorm] = useState(0)
  const value = normToValue(norm, min, max, curve, bind.param_type)
  const send = useSend()
  const { normByControl, mappings } = useMidi()
  const midiNorm = normByControl.get(control.id)
  const mapping = mappings.find((m) => m.controlId === control.id)

  useEffect(() => {
    if (midiNorm == null) return
    setNorm(midiNorm)
    send({
      type: 'set',
      path: bind.path,
      value: normToValue(midiNorm, min, max, curve, bind.param_type),
    } satisfies SetMessage)
  }, [midiNorm, bind.path, bind.param_type, min, max, curve, send])

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const n = Number(e.target.value)
    setNorm(n)
    const msg: SetMessage = {
      type: 'set',
      path: bind.path,
      value: normToValue(n, min, max, curve, bind.param_type),
    }
    send(msg)
  }

  return (
    <div className="widget">
      <div className="widget-label">
        {label}
        {mapping && (
          <span className="widget-midi">
            MIDI: {mapping.sig.type} #{mapping.sig.data1} ch{mapping.sig.channel}
          </span>
        )}
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.001}
        value={norm}
        onChange={onChange}
      />
      <div className="widget-value">{value}</div>
      <div className="widget-path">{bind.path}</div>
    </div>
  )
}
