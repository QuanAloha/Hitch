# Hitch

- `backend/`  FastAPI + PostgreSQL + Valhalla
- `frontend/` Vite + React (proxies `/api/*` to the backend, stripping the `/api` prefix)

```bash
cp .env.example .env     # set SECRET_KEY and OSM_PBF_URL
docker compose up --build
```
App: http://localhost:5173 · API docs: http://localhost:8000/docs

Valhalla builds its tiles on first start (minutes for a state-sized extract). Until it finishes,
accepting a shipment returns a 502 from the routing service.
