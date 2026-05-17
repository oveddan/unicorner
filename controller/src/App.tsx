import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  AlternativeOption,
  Control,
  ControllerSpec,
  InboundMessage,
  SceneSummary,
  SurfaceParam,
} from './types'
import { Knob } from './widgets/Knob'
import { Toggle } from './widgets/Toggle'
import { Unsupported } from './widgets/Unsupported'
import { useTDSocket } from './transport/ws'
import { SendContext } from './transport/context'
import { DesignerDrawer } from './DesignerDrawer'
import { MidiMonitor } from './MidiMonitor'
import { MidiLearnModal } from './MidiLearnModal'

// Default before TD sends us the real surface_path in the schema. Matches the
// legacy POC-0/POC-5 layout so the app still works against an old setup.
const DEFAULT_SURFACE_PATH = '/project1/controller_surface'

type GenStatus = 'idle' | 'thinking' | 'error'
type SummaryStatus = 'idle' | 'thinking' | 'error'

function summaryStorageKey(scene: string) {
  return `unicorner.scene_summary.${scene || 'default'}`
}

function loadSummary(scene: string): SceneSummary | null {
  try {
    const raw = localStorage.getItem(summaryStorageKey(scene))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.text === 'string') return parsed as SceneSummary
    return null
  } catch {
    return null
  }
}

function saveSummary(scene: string, s: SceneSummary | null) {
  try {
    if (s === null) {
      localStorage.removeItem(summaryStorageKey(scene))
      return
    }
    localStorage.setItem(summaryStorageKey(scene), JSON.stringify(s))
  } catch {
    /* localStorage full or disabled — degrade silently */
  }
}

function paramToControl(param: SurfaceParam, surfacePath: string): Control {
  const path = `${surfacePath}/${param.name}`
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
  const [scene, setScene] = useState<string>('')
  const [surfacePath, setSurfacePath] = useState<string>(DEFAULT_SURFACE_PATH)
  const [lastSpec, setLastSpec] = useState<ControllerSpec | null>(null)
  const [alternatives, setAlternatives] = useState<AlternativeOption[] | null>(null)
  const [genStatus, setGenStatus] = useState<GenStatus>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [learnOpen, setLearnOpen] = useState(false)
  const [sceneSummary, setSceneSummary] = useState<SceneSummary | null>(null)
  const [summaryStatus, setSummaryStatus] = useState<SummaryStatus>('idle')

  // Reload (or clear) the persisted summary whenever the active scene
  // changes. The summary is scene-scoped — same as the chat log.
  useEffect(() => {
    setSceneSummary(loadSummary(scene))
  }, [scene])

  const updateSceneSummary = useCallback((next: SceneSummary | null) => {
    setSceneSummary(next)
    saveSummary(scene, next)
  }, [scene])

  const onMessage = useCallback((msg: InboundMessage) => {
    switch (msg.type) {
      case 'schema':
        console.log(`[app] schema: ${msg.params.length} param(s)`, msg.params)
        setParams(msg.params)
        if (msg.scene) setScene(msg.scene)
        if (msg.surface_path) setSurfacePath(msg.surface_path)
        // schema arriving after thinking means the regen succeeded
        setGenStatus((s) => (s === 'thinking' ? 'idle' : s))
        break
      case 'spec':
        console.log('[app] spec', msg.spec)
        setScene(msg.scene)
        setLastSpec(msg.spec)
        setGenStatus('idle')
        setErrorMsg(null)
        break
      case 'thinking':
        setGenStatus('thinking')
        setErrorMsg(null)
        if (msg.scene) setScene(msg.scene)
        break
      case 'error':
        console.warn('[app] generator error:', msg.msg)
        setGenStatus('error')
        setErrorMsg(msg.msg)
        break
      case 'scene_changed':
        console.log('[app] scene_changed', msg.scene)
        setScene(msg.scene)
        if (msg.spec) setLastSpec(msg.spec)
        // Mark the cached summary stale rather than wiping it — the user's
        // edits stay visible, but with a banner suggesting a re-scan.
        setSceneSummary((prev) => {
          if (!prev) return prev
          const next = { ...prev, stale: true }
          saveSummary(msg.scene, next)
          return next
        })
        break
      case 'alternatives':
        console.log(`[app] alternatives: ${msg.alternatives.length}`, msg.alternatives)
        setScene(msg.scene)
        setAlternatives(msg.alternatives)
        setGenStatus('idle')
        setErrorMsg(null)
        break
      case 'understand_thinking':
        console.log('[app] understand_thinking', msg.scene)
        setSummaryStatus('thinking')
        setErrorMsg(null)
        break
      case 'scene_summary':
        console.log('[app] scene_summary', msg.scene, msg.summary?.length, 'chars')
        setSummaryStatus('idle')
        setSceneSummary((prev) => {
          // Don't overwrite a user-edited summary without confirmation —
          // show the new text alongside the edit by replacing only when
          // the prior summary was either absent or auto-generated.
          if (prev && prev.editedByUser) {
            console.warn('[app] scene_summary arrived but local edit exists; preserving edit')
            const next = { ...prev, stale: false }
            saveSummary(msg.scene, next)
            return next
          }
          const next: SceneSummary = {
            text:         msg.summary,
            generatedAt:  Date.now(),
            stale:        false,
            editedByUser: false,
          }
          saveSummary(msg.scene, next)
          return next
        })
        break
    }
  }, [])

  const { status, send } = useTDSocket({ onMessage })

  const onSpecRendered = useCallback(() => {
    // hook for telemetry / future analytics; intentionally a no-op for v1
  }, [])

  // The rendered controls (from the WS schema) are the source of truth for
  // both display and MIDI learn. Synthesize a ControllerSpec-shaped view so
  // MidiLearnModal can iterate and pick — IDs match Knob's control.id
  // (which is param.name), so MIDI mappings drive the right widget.
  const renderedControls = useMemo<Control[] | null>(
    () => (params && params.length > 0
      ? params.map((p) => paramToControl(p, surfacePath))
      : null),
    [params, surfacePath],
  )

  const learnSpec = useMemo<ControllerSpec | null>(
    () => (renderedControls
      ? {
          schema_version: '0.1',
          scene_id:       scene || 'default',
          controls:       renderedControls,
        }
      : null),
    [renderedControls, scene],
  )

  return (
    <SendContext value={send}>
      <div className="app">
        <header>
          <h1>Unicorner Controller</h1>
          <div className="scene">surface: {surfacePath}</div>
          <div className={`wsbadge wsbadge-${status}`}>td: {status}</div>
          <button
            className="new-midi-btn"
            onClick={() => setLearnOpen(true)}
            disabled={!learnSpec}
          >
            + New MIDI Connection
          </button>
        </header>
        {status !== 'open' && !params && (
          <div className="loading">Waiting for TouchDesigner…</div>
        )}
        {status === 'open' && !params && (
          <div className="loading">Connected. Waiting for schema…</div>
        )}
        {renderedControls && renderedControls.length === 0 && (
          <div className="error">
            Connected, but the controller surface is empty. Open the designer
            panel (⚙) and describe the controller you want.
          </div>
        )}
        {renderedControls && renderedControls.length > 0 && (
          <div className={`grid ${genStatus === 'thinking' ? 'grid-regen' : ''}`}>
            {renderedControls.map((c) => renderControl(c))}
          </div>
        )}
        {genStatus === 'thinking' && (
          <div className="regen-overlay">Regenerating…</div>
        )}
        <MidiMonitor />
        {learnOpen && learnSpec && (
          <MidiLearnModal spec={learnSpec} onClose={() => setLearnOpen(false)} />
        )}
      </div>
      <DesignerDrawer
        scene={scene}
        status={genStatus}
        errorMsg={errorMsg}
        lastSpec={lastSpec}
        alternatives={alternatives}
        sceneSummary={sceneSummary}
        summaryStatus={summaryStatus}
        onSpecRendered={onSpecRendered}
        onAlternativesConsumed={() => setAlternatives(null)}
        onSceneSummaryChange={updateSceneSummary}
      />
    </SendContext>
  )
}
