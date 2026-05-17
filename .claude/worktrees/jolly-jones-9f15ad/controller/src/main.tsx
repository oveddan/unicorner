import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './App.css'
import App from './App.tsx'
import { MidiProvider } from './midi/MidiProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MidiProvider>
      <App />
    </MidiProvider>
  </StrictMode>,
)
