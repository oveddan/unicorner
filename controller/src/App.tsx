import { useCallback, useState } from 'react'
import type { Control, InboundMessage, SurfaceParam } from './types'
import { Knob } from './widgets/Knob'
import { Toggle } from './widgets/Toggle'
import { Unsupported } from './widgets/Unsupported'
import { useTDSocket } from './transport/ws'
import { SendContext } from './transport/context'

const SURFACE_PATH = '/project1/controller_surface'

function paramToControl(param: SurfaceParam): Control {
  const path = `${SURFACE_PATH}/${param.name}`
  switch (param.type) {
    case 'float':
    case 'int':
      return {
        id: param.name,
        type: 'knob',
        label: param.label,
        bind: {
          path,
          param_type: param.type,
          min: param.min,
          max: param.max,
          curve: 'linear',
        },
      }
    case 'bool':
      return {
        id: param.name,
        type: 'toggle',
        label: param.label,
        bind: { path, param_type: 'bool' },
      }
    case 'pulse':
      return {
        id: param.name,
        type: 'button',
        label: param.label,
        bind: { path, param_type: 'pulse' },
      }
  }
}

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

export default function App() {
  const [params, setParams] = useState<SurfaceParam[] | null>(null)

  const onMessage = useCallback((msg: InboundMessage) => {
    if (msg.type === 'schema') {
      console.log(`[app] schema received: ${msg.params.length} param(s)`, msg.params)
      setParams(msg.params)
    }
  }, [])

  const { status, send } = useTDSocket({ onMessage })

  return (
    <SendContext value={send}>
      <div className="app">
        <header>
          <h1>Unicorner Controller</h1>
          <div className="scene">surface: {SURFACE_PATH}</div>
          <div className={`wsbadge wsbadge-${status}`}>td: {status}</div>
        </header>
        {status !== 'open' && !params && (
          <div className="loading">Waiting for TouchDesigner…</div>
        )}
        {status === 'open' && !params && (
          <div className="loading">Connected. Waiting for schema…</div>
        )}
        {params && params.length === 0 && (
          <div className="error">
            Connected, but the controller surface is empty. Add custom params to{' '}
            <code>{SURFACE_PATH}</code> in TouchDesigner.
          </div>
        )}
        {params && params.length > 0 && (
          <div className="grid">{params.map((p) => renderControl(paramToControl(p)))}</div>
        )}
      </div>
    </SendContext>
  )
}
