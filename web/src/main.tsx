import React from 'react'
import { createRoot } from 'react-dom/client'
import { SolarProvider } from '@solar-icons/react'
import App from './App'
import { Overlays } from './overlays'
import { UpdateScreen } from './updating'
import { WhatsNew } from './whatsnew'
import { applyScale, watchPixelRatio } from './theme'
import './index.css'

applyScale()  // restore the saved interface size before first paint
watchPixelRatio()

// A demo build answers the API in the browser (see demo.ts), and has to be ready before
// the first request. Anywhere else this is resolved already and the import never loads.
const ready = import.meta.env.VITE_DEMO ? import('./demo').then((d) => d.installDemo()) : Promise.resolve()
// Under `vite` only: `?update` stands in for the desktop app's bridge and plays an install, so
// the update screen can be seen in a browser (mock/desktop.ts).
const bridge = import.meta.env.DEV && new URLSearchParams(location.search).has('update')
  ? import('../mock/desktop').then((m) => m.installDesktopMock())
  : Promise.resolve()

Promise.all([ready, bridge]).then(() => createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SolarProvider value={{ weight: 'Linear', size: 19 }}>
      <App />
      <Overlays />
      <UpdateScreen />
      <WhatsNew />
    </SolarProvider>
  </React.StrictMode>,
))
