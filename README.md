# some_sim

A small FastAPI + React (Vite) app. FastAPI serves the API under `/api` and the built React frontend.

## Run

```sh
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
uvicorn backend.main:app --port 8000   # http://localhost:8000
```

## Dev (hot reload)

```sh
uvicorn backend.main:app --reload --port 8000
cd frontend && npm run dev             # http://localhost:5173, proxies /api
```
