# Frontend ↔ backend integration handoff

This note lists the data the current frontend needs from the API and a suggested contract for connecting the fulfillment and dispatch algorithm. Endpoint names below are proposals; agree on final paths with the backend owner before implementation.

## What exists today

`backend/app/main.py` currently exposes:

| Endpoint | Current behavior |
| --- | --- |
| `GET /health` | API health check |
| `GET /health/db` | Database connectivity check |
| `POST /api/route` | Low-level Valhalla routing adapter. This is not a frontend route-creation endpoint and should not be called by the frontend. |

There are not yet API endpoints for orders, seller pickup requests, pickup runs, freight summaries, driver vehicles, availability, route assignments, stop completion, or payout receipts.

The frontend currently fills those gaps with hard-coded examples and browser `localStorage`. The Vite dev server proxies `/api` and `/health` to FastAPI and `/geocoding` to the local Nominatim container. Seller-entered pickup, delivery, and carrier drop-off addresses are searched only after the user clicks **Find address**; a selected result supplies coordinates for the request. No client-side autocomplete is used.

## Product rules the API should preserve

- **Traditional business shipper:** order ingestion and predicted shipments can come from the business integration/API. The backend assigns local pickup runs and their windows; the shipper sees what to have ready and when. Do not let this view choose a pickup window per order.
- **Etsy and Facebook Marketplace sellers:** they create individual pickup requests with product, package count, packed dimensions, pickup address, destination, and a time window when the item is available. Return a request status immediately, then attach a driver/run/window when dispatch has assigned one.
- **Etsy home shops:** they can also prepare a carrier batch containing multiple packed parcels, choose the carrier and destination post office/ship center, and provide one home pickup window. Dispatch may combine pickups from several Etsy sellers in one neighborhood and route the parcels together to the same carrier facility. A route can include local customer deliveries and carrier drop-offs alongside those pickups.
- **Local vs. long-distance:** local independent drivers serve Logan, Tooele, Ogden, Park City, Salt Lake City, Lehi, and Provo. Long-distance/out-of-area orders should remain assigned to a parcel carrier or 3PL and be identified separately from local-driver work.
- **Drivers:** an availability block references a saved vehicle. Capacity comes from the selected vehicle and is not entered as an arbitrary package count during availability scheduling.
- **Route lock:** the backend is the source of truth. Lock a route 12 hours before the availability start and return the lock state and lock timestamp. The UI countdown/state is only a display of that server value.
- **Route grouping:** dispatch may combine orders from multiple sellers and customers. The UI should display the resulting route and stop order; it should not group orders itself.

## Suggested API surface

### 1. Shipper orders and fulfillment

| Method and path | Purpose |
| --- | --- |
| `GET /api/shipper/orders?from=...&to=...` | Orders and current fulfillment state for the shipper. Include local and 3PL/parcel-carrier orders, or provide a clear filter. |
| `GET /api/shipper/pickup-runs?date=YYYY-MM-DD` | Backend-assigned local runs, pickup windows, driver, vehicle, and the order IDs the shipper must have ready. |
| `GET /api/shipper/freight-summary?period=week\|month&from=...` | Independent-driver vs. FedEx/other-carrier counts for the freight chart and totals. |

Each order should provide at least:

```json
{
  "id": "order-4821",
  "source": "etsy",
  "product_name": "Ceramic tableware",
  "quantity": 1,
  "dimensions": { "length": 18, "width": 14, "height": 8, "unit": "in" },
  "pickup": { "label": "Sugar House", "address": "...", "lat": 40.73, "lon": -111.86 },
  "delivery": { "label": "The Avenues", "address": "...", "lat": 40.78, "lon": -111.88 },
  "fulfillment_type": "local_driver",
  "status": "assigned",
  "pickup_run_id": "run-204",
  "pickup_window": { "start": "2026-11-07T10:30:00-07:00", "end": "2026-11-07T11:00:00-07:00" },
  "driver_id": "driver-17",
  "driver_name": "Jordan Lee"
}
```

Use `fulfillment_type` values such as `local_driver`, `fedex`, and `third_party_logistics` (or document the chosen enum). For out-of-area orders, return the actual carrier/3PL and its handoff status rather than a local pickup run. Include pickup and delivery coordinates whenever available so the driver map can render the route without geocoding addresses in the browser.

The freight summary should use a consistent measure (for example, order count or package count) and return that measure in the response. The current chart labels its values as orders. Include the time buckets used for each bar, the independent-driver value, the FedEx/other-carrier value, and totals for the selected week/month.

### 2. Home-seller pickup requests

| Method and path | Purpose |
| --- | --- |
| `POST /api/seller/pickup-requests` | Create an Etsy or Facebook Marketplace pickup request. |
| `GET /api/seller/pickup-requests?source=etsy\|facebook_marketplace` | List the seller's requests and their latest dispatch status. |
| `GET /api/seller/pickup-requests/{request_id}` | Refresh one request after dispatch changes it. |

Suggested create body:

```json
{
  "source": "facebook_marketplace",
  "product_name": "Used television",
  "quantity": 1,
  "dimensions": { "length": 48, "width": 28, "height": 6, "unit": "in" },
  "notes": "Fragile screen; please keep upright",
  "pickup": { "address": "...", "lat": 40.72, "lon": -111.86 },
  "destination": { "address": "...", "lat": 40.76, "lon": -111.89 },
  "available_window": { "start": "2026-11-07T09:00:00-07:00", "end": "2026-11-07T12:00:00-07:00" }
}
```

The response should return the request ID, status (for example, `awaiting_driver`, `assigned`, `picked_up`, `delivered`, `cancelled`), any assigned driver/run, and the confirmed pickup window. Keep a seller-provided availability window distinct from a backend-confirmed pickup window.

Dimensions must be stored with units and passed into dispatch/capacity checks. A TV and a small handheld item must not be treated as interchangeable just because both are one package.

Pickup and delivery addresses selected in the current seller forms include latitude/longitude in browser demo records. When wiring these requests to the API, pass the chosen coordinates with the address and use them directly for routing rather than geocoding the same address again. The local Nominatim instance currently imports the Utah Geofabrik extract, so the demo geocoder is intended for Utah pickup/delivery locations; out-of-area parcel-carrier destinations may remain address-only. Do not silently switch residential addresses to the public Nominatim service.

### 2a. Etsy carrier batches

| Method and path | Purpose |
| --- | --- |
| `POST /api/seller/carrier-batches` | Create a batch of Etsy parcels for a selected carrier and carrier drop-off facility. |
| `GET /api/seller/carrier-batches` | List the seller's batches and their pickup/dispatch status. |
| `GET /api/seller/carrier-batches/{batch_id}` | Refresh a batch after dispatch assigns its pickup. |

Suggested create body:

```json
{
  "source": "etsy",
  "batch_name": "Friday shop orders",
  "carrier": "usps",
  "dropoff": { "facility_name": "Downtown Post Office", "address": "...", "lat": 40.768, "lon": -111.891 },
  "pickup": { "address": "Home studio address", "lat": 40.73, "lon": -111.86 },
  "available_window": { "start": "2026-11-07T09:00:00-07:00", "end": "2026-11-07T12:00:00-07:00" },
  "packages": [
    { "product_name": "Handmade mug", "dimensions": { "length": 10, "width": 8, "height": 7, "unit": "in" } },
    { "product_name": "Ceramic vase", "dimensions": { "length": 14, "width": 10, "height": 10, "unit": "in" } }
  ]
}
```

Return the batch ID, package count, selected carrier/facility, status, and any assigned pickup window, driver, and route. Keep the seller's available window separate from the confirmed dispatch window. Treat the carrier drop-off as a route stop with type `carrier_dropoff` and coordinates. Dispatch owns consolidation: it can collect compatible batches from multiple nearby Etsy sellers, then make one stop at a shared post office or ship center. Include the batch/package IDs represented by each pickup and carrier-drop-off stop so drivers and sellers can see which parcels travel together. The seller selects the carrier and facility for their batch; the frontend does not build routes or choose which batches dispatch combines.

### 3. Driver vehicle garage and availability

| Method and path | Purpose |
| --- | --- |
| `GET /api/driver/vehicles` | List the driver's garage. |
| `POST /api/driver/vehicles` | Save a vehicle from VIN, plate, and plate state; return decoded vehicle details and capacity. |
| `DELETE /api/driver/vehicles/{vehicle_id}` | Remove a saved vehicle. |
| `GET /api/driver/availability?from=...&to=...` | List **every availability block the driver has posted**, including blocks that are still forming and blocks that have a locked route. This is the driver's home schedule. |
| `POST /api/driver/availability` | Add a block with a `vehicle_id`, starting location, availability window, and fuel level. |
| `DELETE /api/driver/availability/{block_id}` | Cancel an eligible future availability block. |

Vehicle responses should include a stable ID, VIN (avoid returning the full VIN where the UI does not need it), plate/state, year/make/model/body class, and a capacity value with units and provenance. The frontend currently calls NHTSA vPIC directly to decode VIN attributes and estimates standard package spots from body class; a backend vehicle endpoint should become the authoritative persisted record and return the capacity estimate or measured capacity the dispatch algorithm actually uses.

An availability response should include:

```json
{
  "id": "availability-123",
  "vehicle_id": "vehicle-9",
  "vehicle_name": "2019 Toyota RAV4",
  "capacity": { "value": 8, "unit": "standard_package_spots" },
  "start_location": { "label": "Sugar House", "lat": 40.72, "lon": -111.86 },
  "window": { "start": "2026-11-07T10:00:00-07:00", "end": "2026-11-07T14:00:00-07:00" },
  "route_status": "forming",
  "route_id": null,
  "route": null,
  "locks_at": "2026-11-06T22:00:00-07:00",
  "locked_at": null,
  "estimated_fill_percent": 68,
  "confirmed_package_spots": 5
}
```

Return `route_status` as `forming`, `locked`, or `completed` (or document the selected state machine). Return server-computed `locks_at`, `locked_at`, estimated fill, and confirmed capacity usage. The frontend currently recalculates the 12-hour lock locally; replace that with the API's `route_status` so clock and timezone behavior cannot disagree with dispatch.

### 4. Driver route, stops, navigation, and payout receipt

| Method and path | Purpose |
| --- | --- |
| `GET /api/driver/routes/{route_id}` | Return route detail, geometry, stops, capacity/fill, lock status, and receipt. |
| `POST /api/driver/routes/{route_id}/start` | Persist that the driver started the route. |
| `POST /api/driver/routes/{route_id}/stops/{stop_id}/complete` | Persist pickup/drop-off completion and return the next stop. |

The availability list is the source for the driver's full schedule. Each item should include the availability block even if the algorithm has not attached a route yet. Return `route: null` while a block is still forming, and include a route ID/summary when a route is assigned. The UI sorts all blocks by start time; a block inside the 12-hour lock window appears with its locked route and can be opened as a dedicated route page.

For each route returned by `GET /api/driver/routes/{route_id}`, include:

- availability block ID and route state (`forming`, `locked`, `started`, `completed`)
- ordered stops, each with stable ID, type (`start`, `pickup`, `dropoff`, or `carrier_dropoff`), label/address, latitude, longitude, order IDs, and any pickup instructions
- route geometry (prefer GeoJSON `LineString` coordinates in `[lon, lat]` order; if using an encoded polyline, document its precision)
- expected fill percent and confirmed package/cargo usage
- locked timestamp and the final pickup/drop-off windows
- per-order/per-shipper payout lines and route total, available after lock
- fuel suggestions, if supplied by the backend (station name/address/coordinates, price, price timestamp/source, and detour distance/time)

The existing `POST /api/route` is a low-level Valhalla adapter, not a frontend workflow. The frontend must not submit an ordered stop list or ask the backend to build a route. The dispatch algorithm should call Valhalla from the backend as needed, persist the resulting Hitch route, and serve it to the frontend through `GET /api/driver/routes/{route_id}`. The frontend only reads the route and renders its geometry, stops, payout, and navigation links.

Each stop row in the driver UI opens Google Maps using the stop's latitude and longitude. The frontend also has a start-route/mark-stop-complete interaction; those actions should call the endpoints above instead of relying on browser storage.

### 5. Fuel suggestion data

Fuel suggestions are currently static frontend demo content. To show an actual cheapest nearby fuel stop, provide station identity, coordinates, price, price units, source/update time, and detour cost. Prices change, so include an `updated_at` timestamp and do not label stale data as current. This can be added to route detail or exposed as `GET /api/driver/routes/{route_id}/fuel-stops`.

## Integration priorities

1. **Dispatch data contract:** availability blocks, route status, 12-hour lock timestamp, ordered stops, and capacity/fill estimate. This drives the core driver view.
2. **Route details:** geometry plus coordinates and stop IDs so the map, Google Maps links, and route progress all refer to the same backend route.
3. **Shipper fulfillment:** business orders, backend-assigned pickup runs, driver names, confirmed windows, and local-vs-3PL classification.
4. **Home-seller requests:** create/list pickup requests with dimensions and seller availability windows; dispatch can then attach them to multi-seller routes.
5. **Vehicle garage and receipt:** persist vehicles/capacity and return locked-route payout lines.
6. **Freight analytics and fuel pricing:** weekly/monthly carrier-vs-FedEx aggregates and timestamped fuel suggestions.

## Cross-cutting API details to agree

- Authentication and how a request identifies the active shipper, seller, or driver.
- Time format: ISO 8601 with timezone offsets; use UTC internally and return offsets or UTC timestamps consistently.
- Stable IDs, pagination for order history, sorting, and filters.
- Error response shape for validation, unavailable dispatch, stale route state, and VIN/vehicle lookup failures.
- How the UI learns that a route changed (polling interval initially; server-sent events/WebSocket later if needed).
- Ownership and retention of VIN/plate data, seller addresses, and customer delivery addresses.

## Frontend demo storage to replace

| Browser key or fixture | Intended backend replacement |
| --- | --- |
| `shipperOrders`, `freightCycles` | Shipper orders, pickup runs, and freight summary endpoints |
| `hitch-seller-pickup-requests` | Seller pickup-request endpoints |
| `hitch-etsy-carrier-batches` | Etsy carrier-batch endpoints |
| `hitch-driver-vehicle-garage` | Driver vehicle garage endpoints |
| `hitch-driver-availability` | Driver availability and route endpoints |
| `hitch-driver-route-progress` | Route start and stop-completion endpoints |
| `hitch-theme` | Keep local; this is a user display preference, not dispatch data |

