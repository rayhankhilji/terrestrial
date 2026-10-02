# Terrestrial web app

Vite + React + TypeScript single-screen UI. Run the API first (`uv run uvicorn api.main:app --port 8000` from the repo root), then:

```bash
npm install
npm run dev   # http://localhost:5173, proxies /api to :8000
```
