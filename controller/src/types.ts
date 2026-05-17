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

export type GenerateMessage = {
  type: 'generate'
  prompt: string
  scene: string
  history: Array<{ role: 'user' | 'assistant'; content: string }>
}

export type PickAlternativeMessage = {
  type: 'pick_alternative'
  scene: string
  alt_id: string
}

export type OutboundMessage = SetMessage | GenerateMessage | PickAlternativeMessage

export type SurfaceParamType = 'float' | 'int' | 'bool' | 'pulse'

export type SurfaceParam = {
  name: string
  label: string
  type: SurfaceParamType
  min?: number
  max?: number
  default?: number | boolean
}

export type SchemaMessage = {
  type: 'schema'
  scene?: string
  surface_path?: string
  params: SurfaceParam[]
}

export type ThinkingMessage = {
  type: 'thinking'
  scene?: string
}

export type SpecMessage = {
  type: 'spec'
  scene: string
  spec: ControllerSpec
}

export type ErrorMessage = {
  type: 'error'
  msg: string
}

export type SceneChangedMessage = {
  type: 'scene_changed'
  scene: string
  spec?: ControllerSpec
}

export type AlternativeOption = {
  id: string
  label: string
  description: string
}

export type AlternativesMessage = {
  type: 'alternatives'
  scene: string
  alternatives: AlternativeOption[]
}

export type InboundMessage =
  | SchemaMessage
  | ThinkingMessage
  | SpecMessage
  | ErrorMessage
  | SceneChangedMessage
  | AlternativesMessage
