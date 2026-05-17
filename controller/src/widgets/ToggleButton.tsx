import { useEffect, useRef, useState } from 'react'
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

export function ToggleButton({ id, label, path, onLabelChange }: Props) {
  const [on, setOn] = useState(false)
  const send = useSend()
  const { normByControl, mappings } = useMidi()
  const midiNorm = normByControl.get(id)
  const mapping = mappings.find((m) => m.controlId === id)
  const prevNormRef = useRef(0)

  function doToggle() {
    setOn((curr) => {
      const next = !curr
      send({ type: 'set', path, value: next } satisfies SetMessage)
      return next
    })
  }

  useEffect(() => {
    if (midiNorm == null) return
    const prev = prevNormRef.current
    prevNormRef.current = midiNorm
    if (prev <= 0 && midiNorm > 0) {
      doToggle()
    }
  }, [midiNorm])

  return (
    <button
      type="button"
      className={`signal-button${on ? ' is-on' : ''}`}
      aria-pressed={on}
      onClick={doToggle}
    >
      <span className="signal-button-label">
        <EditableLabel value={label} onChange={onLabelChange} />
      </span>
      {mapping && (
        <span className="widget-midi">
          {mapping.sig.type} #{mapping.sig.data1} ch{mapping.sig.channel}
        </span>
      )}
    </button>
  )
}
