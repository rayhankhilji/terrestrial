import { useEffect, useState } from 'react'

export default function App() {
  const [status, setStatus] = useState('checking API…')

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { status: string }) => setStatus(`API ${d.status}`))
      .catch((e: Error) => setStatus(`API unreachable: ${e.message}`))
  }, [])

  return (
    <main className="boot">
      <h1>Terrestrial</h1>
      <p>Dark-vessel detection for the Black Sea</p>
      <p className="muted">{status}</p>
    </main>
  )
}
