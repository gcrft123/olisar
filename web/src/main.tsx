import React from 'react'
import { createRoot } from 'react-dom/client'
import { SolarProvider } from '@solar-icons/react'
import App from './App'
import { Overlays } from './overlays'
import { applyAccent } from './theme'
import './index.css'

applyAccent()  // restore the saved accent color before first paint

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SolarProvider value={{ weight: 'Linear', size: 19 }}>
      <App />
      <Overlays />
    </SolarProvider>
  </React.StrictMode>,
)
