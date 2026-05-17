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

// ---------------------------------------------------------------------------
// Routings — DJay Pro signals wired directly into scene parameters.
//
// Present in a ControllerSpec only when a DJay catalog was available at
// generate-time (the COMP's `Djaypath` param resolves to a CHOP with named
// channels). The AI is allowed to emit routings alongside the manual
// `controls`; the apply step in TD builds the routing infrastructure
// (LFO CHOPs, CHOP Execute DATs, bound expressions) and tags it
// `unicorner.routing` for idempotent teardown.
//
// `*_control_id` fields reference the id of a Control elsewhere in the same
// spec (not a surface path), so the routing applier can resolve the surface
// param after the surface is built.
// ---------------------------------------------------------------------------

export type LfoSyncTarget = {
  path: string
  param: string
  min: number
  max: number
  curve?: Curve
}

export type DirectRouting = {
  id: string
  type: 'direct'
  label: string
  djay_channel: string
  target_path: string
  target_param: string
  min?: number
  max?: number
  curve?: Curve
  blend_control_id?: string
}

export type LfoSyncRouting = {
  id: string
  type: 'lfo_sync'
  label: string
  lfo_name: string
  bpm_channel?: string
  beats_per_cycle?: number
  rate_multiplier_control_id?: string
  targets: LfoSyncTarget[]
}

export type BarResetRouting = {
  id: string
  type: 'bar_reset'
  label: string
  djay_channel: string
  target_lfo_path: string
}

/** Beat-triggered advancing value. Scaffolds the CHOP chain:
 *  djay[djay_channel] -> Select -> Envelope (exp, attack/decay) -> Speed -> bind target.
 *  Use for "advance per beat" behavior (cycling palette, ticking counter,
 *  per-beat steps). Pair with rate_multiplier_control_id so the DJ can
 *  dial the speed without re-prompting. */
export type TriggeredSpeedTarget = {
  path: string
  param: string
  min: number
  max: number
  curve?: Curve
}

export type TriggeredSpeedRouting = {
  id: string
  type: 'triggered_speed'
  label: string
  djay_channel: string
  envelope_decay?: number
  envelope_attack?: number
  rate_multiplier_control_id?: string
  wrap?: boolean
  targets: TriggeredSpeedTarget[]
}

export type Routing =
  | DirectRouting
  | LfoSyncRouting
  | BarResetRouting
  | TriggeredSpeedRouting

export type ControllerSpec = {
  schema_version: '0.1'
  scene_id: string
  rationale?: string
  controls: Control[]
  layout?: string[][]
  routings?: Routing[]
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
  scene_summary?: string
}

export type PickAlternativeMessage = {
  type: 'pick_alternative'
  scene: string
  alt_id: string
}

export type UnderstandSceneMessage = {
  type: 'understand_scene'
  scene: string
}

/** Nuke everything for the active scene: destroy the controller_surface +
 *  any unicorner.routing-tagged ops, clear all server-side cached state
 *  (last spec, pending alternatives/questions, scene summary, routing
 *  targets). The TD side broadcasts an empty schema + a scene_summary-cleared
 *  signal so all connected clients reset in lockstep. */
export type ResetSceneMessage = {
  type: 'reset_scene'
  scene: string
}

export type OutboundMessage =
  | SetMessage
  | GenerateMessage
  | PickAlternativeMessage
  | UnderstandSceneMessage
  | ResetSceneMessage

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
  /** 'choice' = legacy alternatives (server has a spec ready). 'question' = the
   *  model asked a clarifying question; tapping picks an answer and triggers
   *  another generate. */
  kind?: 'choice' | 'question'
}

export type AlternativesMessage = {
  type: 'alternatives'
  scene: string
  alternatives: AlternativeOption[]
  /** Only present when alternatives are question-kind: the assistant's prompt
   *  to the user, rendered above the chips. */
  question?: string
}

export type SceneSummary = {
  text: string
  generatedAt: number
  stale: boolean
  editedByUser: boolean
}

export type SceneSummaryMessage = {
  type: 'scene_summary'
  scene: string
  summary: string
}

export type UnderstandThinkingMessage = {
  type: 'understand_thinking'
  scene: string
}

/** Broadcast by TD after a reset_scene message has been fully processed.
 *  Clients use this to flush their own caches (chat log, draft, summary)
 *  so a reset triggered from one device propagates to all open tabs. */
export type SceneResetMessage = {
  type: 'scene_reset'
  scene: string
}

export type InboundMessage =
  | SchemaMessage
  | ThinkingMessage
  | SpecMessage
  | ErrorMessage
  | SceneChangedMessage
  | AlternativesMessage
  | SceneSummaryMessage
  | UnderstandThinkingMessage
  | SceneResetMessage
