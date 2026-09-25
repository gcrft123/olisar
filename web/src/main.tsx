import React from 'react'
import { createRoot } from 'react-dom/client'
import { SolarProvider } from '@solar-icons/react'
import App from './App'
import { Overlays } from './overlays'
import { applyScale } from './theme'
import './index.css'

applyScale()  // restore the saved interface size before first paint

// A demo build answers the API in the browser (see demo.ts), and has to be ready before
// the first request. Anywhere else this is resolved already and the import never loads.
const ready = import.meta.env.VITE_DEMO ? import('./demo').then((d) => d.installDemo()) : Promise.resolve()

ready.then(() => createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SolarProvider value={{ weight: 'Linear', size: 19 }}>
      <App />
      <Overlays />
    </SolarProvider>
  </React.StrictMode>,
))
