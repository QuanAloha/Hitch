# Hitch

Hackathon project exploring first-mile delivery options for independent carriers and shippers.

Hitch connects local pickup and delivery opportunities with independent drivers, helping make better use of the vehicles already on the road.

## Stack

- **Frontend:** React + Vite (`http://localhost:5173`)
- **API:** FastAPI (`http://localhost:8000`, interactive docs at `/docs`)
- **Routing:** Valhalla (`http://localhost:8002`)
- **Database:** PostgreSQL 16 (`localhost:5432`)

## Run locally with Docker Compose

1. Optionally copy `.env.example` to `.env` and change the local PostgreSQL password.
2. Start the stack:

   ```sh
   docker compose up --build
   ```

3. Open the frontend at [http://localhost:5173](http://localhost:5173). The landing page links to both demo views:
   - Shipper dashboard: [http://localhost:5173/shipper](http://localhost:5173/shipper)
   - Phone-first carrier view: [http://localhost:5173/carrier](http://localhost:5173/carrier)
4. To open the demo on your phone over Tailscale, use your laptop's Tailscale IP in place of `localhost`, for example `http://100.x.y.z:5173/carrier`. Keep the Compose stack running and make sure both devices are connected to your tailnet.

The API health endpoint is at [http://localhost:8000/health](http://localhost:8000/health) and API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

The carrier demo has a bottom navigation bar for Home, Availability, Activity, and Profile. Use **Availability** to set a starting location, departure fuel, vehicle package capacity, and available time window. Availability blocks are saved in that browser's local storage; the dashboard displays sample shipper bookings and locks dispatch when departure is within 24 hours.

The first Valhalla startup downloads the Utah OpenStreetMap extract and builds routing graph tiles. This can take several minutes and requires several gigabytes of disk space. The generated graph and database are kept in Docker volumes across restarts. To delete all stored data, run `docker compose down -v`.

## API starter endpoints

- `GET /health` — API status
- `GET /health/db` — PostgreSQL connectivity check
- `POST /api/route` — forwards a Valhalla route request. Example body:

  ```json
  {
    "locations": [
      { "lat": 40.7608, "lon": -111.891 },
      { "lat": 40.2338, "lon": -111.6585 }
    ],
    "costing": "auto"
  }
  ```

The frontend proxies `/api` and `/health` requests to FastAPI in Compose. OSM data is provided by [Geofabrik](https://download.geofabrik.de/north-america/us/utah.html) and routing is powered by [Valhalla](https://github.com/valhalla/valhalla).
