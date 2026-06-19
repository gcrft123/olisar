import React from 'react'
import { createRoot } from 'react-dom/client'
import { SolarProvider } from '@solar-icons/react'
import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SolarProvider value={{ weight: 'Linear', size: 19 }}>
      <App />
    </SolarProvider>
  </React.StrictMode>,
)
