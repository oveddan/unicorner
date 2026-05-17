import { useEffect, useState } from 'react'
import type { ControllerSpec } from './types'
import { ManualSlider } from './widgets/ManualSlider'
import { ToggleButton } from './widgets/ToggleButton'
import { useTDSocket } from './transport/ws'
import { SendContext } from './transport/context'
import { MidiLearnModal } from './MidiLearnModal'
import { MidiMonitor } from './MidiMonitor'
import { useMidi } from './midi/MidiProvider'

type Theme = 'dark' | 'clear'

const SLIDER_DEFS = [
  { id: 'slider1', defaultLabel: 'Slider 1', path: '/project1/slider1_target/value0' },
  { id: 'slider2', defaultLabel: 'Slider 2', path: '/project1/slider2_target/value0' },
  { id: 'slider3', defaultLabel: 'Slider 3', path: '/project1/slider3_target/value0' },
  { id: 'slider4', defaultLabel: 'Slider 4', path: '/project1/slider4_target/value0' },
] as const

const BUTTON_DEFS = [
  { id: 'btn1', defaultLabel: 'Button 1', path: '/project1/btn1_target/value0' },
  { id: 'btn2', defaultLabel: 'Button 2', path: '/project1/btn2_target/value0' },
  { id: 'btn3', defaultLabel: 'Button 3', path: '/project1/btn3_target/value0' },
  { id: 'btn4', defaultLabel: 'Button 4', path: '/project1/btn4_target/value0' },
] as const

const LABEL_STORAGE_KEY = 'unicorner.labels.v1'

function loadLabels(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LABEL_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveLabels(labels: Record<string, string>) {
  try {
    localStorage.setItem(LABEL_STORAGE_KEY, JSON.stringify(labels))
  } catch {
    // localStorage may be disabled (e.g. privacy mode); editing still works in-memory
  }
}

export default function App() {
  const [spec, setSpec] = useState<ControllerSpec | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [theme, setTheme] = useState<Theme>('dark')
  const [learnOpen, setLearnOpen] = useState(false)
  const [labels, setLabels] = useState<Record<string, string>>(loadLabels)
  const { status, send } = useTDSocket()
  const midi = useMidi()

  const labelFor = (id: string, fallback: string) => labels[id] ?? fallback
  const setLabel = (id: string) => (next: string) =>
    setLabels((prev) => {
      const updated = { ...prev, [id]: next }
      saveLabels(updated)
      return updated
    })

  useEffect(() => {
    fetch(`/specs/a.json?ts=${Date.now()}`, { cache: 'no-store' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data: ControllerSpec) => setSpec(data))
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!learnOpen) return

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setLearnOpen(false)
    }

    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [learnOpen])

  const learnExtras = [
    ...SLIDER_DEFS.map((s) => ({
      id: s.id,
      label: labelFor(s.id, s.defaultLabel),
      type: 'slider',
    })),
    ...BUTTON_DEFS.map((b) => ({
      id: b.id,
      label: labelFor(b.id, b.defaultLabel),
      type: 'toggle-button',
    })),
  ]

  return (
    <SendContext value={send}>
      <div className="app" data-theme={theme}>
        <header className="app-header">
          <div className="title-group">
            <p className="eyebrow">TouchDesigner Signal Manager</p>
            <h1>Unicorner Controller</h1>
          </div>
          <div className="top-actions" aria-label="Controller actions">
            <button
              className="primary-action"
              type="button"
              onClick={() => setLearnOpen(true)}
            >
              Assign MIDI
            </button>
            <button
              className="mode-toggle"
              type="button"
              onClick={() => setTheme((current) => (current === 'dark' ? 'clear' : 'dark'))}
              aria-pressed={theme === 'clear'}
            >
              {theme === 'dark' ? 'Clear Mode' : 'Dark Mode'}
            </button>
          </div>
        </header>
        <main className="manager-shell">
          <section className="status-strip" aria-label="Connection status">
            <div>
              <span>Scene</span>
              <strong>{spec?.scene_id ?? '…'}</strong>
            </div>
            <div className={`wsbadge wsbadge-${status}`}>
              <span>TD</span>
              <strong>{status}</strong>
            </div>
            <div>
              <span>MIDI</span>
              <strong>{midi.status}</strong>
            </div>
          </section>

          {error && <div className="error">Failed to load spec: {error}</div>}

          <div className="card-grid">
            <section className="manager-card manual-card">
              <div className="card-heading">
                <div>
                  <p className="eyebrow">Manual</p>
                  <h2>Controls</h2>
                </div>
                <span className="card-count">{SLIDER_DEFS.length} sliders</span>
              </div>
              <div className="rows">
                {SLIDER_DEFS.map((s) => (
                  <ManualSlider
                    key={s.id}
                    id={s.id}
                    label={labelFor(s.id, s.defaultLabel)}
                    path={s.path}
                    onLabelChange={setLabel(s.id)}
                  />
                ))}
              </div>
            </section>

            <section className="manager-card buttons-card">
              <div className="card-heading">
                <div>
                  <p className="eyebrow">Manual</p>
                  <h2>Buttons</h2>
                </div>
                <span className="card-count">{BUTTON_DEFS.length} buttons</span>
              </div>
              <div className="button-grid">
                {BUTTON_DEFS.map((b) => (
                  <ToggleButton
                    key={b.id}
                    id={b.id}
                    label={labelFor(b.id, b.defaultLabel)}
                    path={b.path}
                    onLabelChange={setLabel(b.id)}
                  />
                ))}
              </div>
            </section>

            <section className="manager-card midi-card">
              <div className="card-heading">
                <div>
                  <p className="eyebrow">MIDI</p>
                  <h2>Incoming Signals</h2>
                </div>
                <span className="card-count">{midi.inputs.length} inputs</span>
              </div>
              <div className="midi-placeholder">
                <div className={`midi-dot midi-dot-${midi.status}`} />
                <div>
                  <strong>{midi.status}</strong>
                  <span>
                    {midi.inputs.length === 0 ? 'No MIDI inputs detected' : midi.inputs.join(', ')}
                  </span>
                </div>
              </div>
            </section>
          </div>

          <section className="manager-card midi-monitor-card">
            <MidiMonitor />
          </section>
        </main>

        {learnOpen && (
          <MidiLearnModal
            spec={null}
            extras={learnExtras}
            onClose={() => setLearnOpen(false)}
          />
        )}
      </div>
    </SendContext>
  )
}
