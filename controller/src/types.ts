export type Curve = 'linear' | 'log' | 'exp'

export type FloatIntBinding = {
  path: string
  param_type: 'float' | 'int'
  min?: number
  max?: number
  curve?: Curve
}

export type BoolBinding = {
  path: string
  param_type: 'bool'
}

export type PulseBinding = {
  path: string
  param_type: 'pulse'
}

export type MacroBinding = {
  path: string
  param_type: 'float' | 'int'
  from: number
  to: number
  curve?: Curve
}

export type KnobControl = {
  id: string
  type: 'knob' | 'slider'
  label: string
  bind: FloatIntBinding
}

export type ToggleControl = {
  id: string
  type: 'toggle'
  label: string
  bind: BoolBinding
}

export type ButtonControl = {
  id: string
  type: 'button'
  label: string
  bind: PulseBinding
}

export type MacroControl = {
  id: string
  type: 'macro'
  label: string
  macro_bindings: MacroBinding[]
}

export type Control = KnobControl | ToggleControl | ButtonControl | MacroControl

export type ControllerSpec = {
  schema_version: '0.1'
  scene_id: string
  rationale?: string
  controls: Control[]
  layout?: string[][]
}

export type SetMessage = {
  type: 'set'
  path: string
  value: number | boolean
}
