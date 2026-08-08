import React from 'react'
import { createRoot } from 'react-dom/client'
import { SolarProvider } from '@solar-icons/react'
import App from './App'
import { Overlays } from './overlays'
import { applyScale } from './theme'
import './index.css'

applyScale()  // restore the saved interface size before first paint

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SolarProvider value={{ weight: 'Linear', size: 19 }}>
      <App />
      <Overlays />
    </SolarProvider>
  </React.StrictMode>,
)
