import type { Control } from '../types'

type Props = { control: Control }

export function Unsupported({ control }: Props) {
  return (
    <div className="widget widget-unsupported">
      <div className="widget-label">{control.label}</div>
      <div className="widget-value">[unsupported: {control.type}]</div>
    </div>
  )
}
