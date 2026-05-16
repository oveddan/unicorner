import { useState } from 'react'
import type { ToggleControl, SetMessage } from '../types'
import { useSend } from '../transport/context'

type Props = { control: ToggleControl }

export function Toggle({ control }: Props) {
  const { bind, label } = control
  const [on, setOn] = useState(false)
  const send = useSend()

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const next = e.target.checked
    setOn(next)
    const msg: SetMessage = { type: 'set', path: bind.path, value: next }
    send(msg)
  }

  return (
    <div className="widget">
      <div className="widget-label">{label}</div>
      <label className="widget-toggle">
        <input type="checkbox" checked={on} onChange={onChange} />
        <span>{on ? 'On' : 'Off'}</span>
      </label>
      <div className="widget-path">{bind.path}</div>
    </div>
  )
}
