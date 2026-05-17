import { useEffect, useRef, useState } from 'react'
import type { ControllerSpec } from './types'
import { useMidi, type MidiEvent } from './midi/MidiProvider'

export type LearnTarget = { id: string; label: string; type: string }

type Props = {
  spec: ControllerSpec | null
  extras?: LearnTarget[]
  onClose: () => void
}

export function MidiLearnModal({ spec, extras, onClose }: Props) {
  const { lastEvent, addMapping } = useMidi()
  const [step, setStep] = useState<'pick' | 'learn'>('pick')
  const [controlId, setControlId] = useState<string | null>(null)
  const [captured, setCaptured] = useState<MidiEvent | null>(null)
  const enteredAtRef = useRef<number>(0)

  const targets: LearnTarget[] = [
    ...(spec?.controls.map((c) => ({ id: c.id, label: c.label, type: c.type })) ?? []),
    ...(extras ?? []),
  ]
  const selectedLabel = targets.find((t) => t.id === controlId)?.label ?? controlId

  useEffect(() => {
    if (step !== 'learn' || !lastEvent) return
    if (lastEvent.time <= enteredAtRef.current) return
    setCaptured(lastEvent)
  }, [lastEvent, step])

  function pickControl(id: string) {
    setControlId(id)
    setCaptured(null)
    enteredAtRef.current = lastEvent?.time ?? 0
    setStep('learn')
  }

  function done() {
    if (!controlId || !captured) return
    addMapping(controlId, {
      device: captured.device,
      channel: captured.channel,
      type: captured.type,
      data1: captured.data1,
    })
    onClose()
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="assign-midi-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <p className="eyebrow">MIDI</p>
            <h2 id="assign-midi-title">Assign MIDI</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="Close MIDI assignment"
          >
            ×
          </button>
        </div>

        {step === 'pick' && (
          <div className="modal-body">
            <p className="modal-copy">Pick a parameter to control:</p>
            <ul className="param-list">
              {targets.map((t) => (
                <li key={t.id}>
                  <button className="param-choice" type="button" onClick={() => pickControl(t.id)}>
                    <strong>{t.label}</strong>
                    <span className="param-meta">
                      {t.id} · {t.type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {step === 'learn' && controlId && (
          <div className="modal-body">
            <p className="modal-copy">
              Selected: <strong>{selectedLabel}</strong>
            </p>
            <p className="modal-copy">Now move the MIDI control you want to assign.</p>
            <div className="learn-box">
              {!captured && <div className="learn-waiting">waiting for MIDI</div>}
              {captured && (
                <div className="learn-captured">
                  <div className="learn-line">
                    <span>device</span>
                    <strong>{captured.device}</strong>
                  </div>
                  <div className="learn-line">
                    <span>channel</span>
                    <strong>{captured.channel}</strong>
                  </div>
                  <div className="learn-line">
                    <span>type</span>
                    <strong>{captured.type}</strong>
                  </div>
                  <div className="learn-line">
                    <span>control #</span>
                    <strong>{captured.data1}</strong>
                  </div>
                  <div className="learn-line">
                    <span>live value</span>
                    <strong>{captured.value}</strong>
                  </div>
                  <div className="learn-bar">
                    <div className="learn-bar-fill" style={{ width: `${captured.norm * 100}%` }} />
                  </div>
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setStep('pick')}>
                Back
              </button>
              <button className="primary" type="button" disabled={!captured} onClick={done}>
                Done
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
