import httpx


class ValhallaError(Exception):
    pass


class ValhallaClient:
    def __init__(self, base_url: str, costing: str = "auto") -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30)
        self.costing = costing

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = await self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ValhallaError(f"Valhalla unreachable: {exc}") from exc
        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", resp.text)
            except ValueError:
                detail = resp.text
            raise ValhallaError(f"Valhalla {path} failed ({resp.status_code}): {detail}")
        return resp.json()

    @staticmethod
    def _locs(points: list[tuple[float, float]]) -> list[dict]:
        return [{"lat": lat, "lon": lon} for lat, lon in points]

    async def time_matrix(self, points: list[tuple[float, float]]) -> list[list[float | None]]:
        """Square matrix of travel times in seconds (None = unreachable)."""
        locs = self._locs(points)
        data = await self._post(
            "/sources_to_targets",
            {"sources": locs, "targets": locs, "costing": self.costing},
        )
        return [[cell.get("time") for cell in row] for row in data["sources_to_targets"]]

    async def route(self, points: list[tuple[float, float]]) -> dict:
        """Turn-by-turn route through the points in the given order."""
        data = await self._post(
            "/route",
            {
                "locations": self._locs(points),
                "costing": self.costing,
                "directions_options": {"units": "kilometers"},
            },
        )
        trip = data["trip"]
        return {
            "distance_km": trip["summary"]["length"],
            "duration_s": trip["summary"]["time"],
            "legs": [
                {
                    "distance_km": leg["summary"]["length"],
                    "duration_s": leg["summary"]["time"],
                    "shape": leg["shape"],  # encoded polyline, precision 6
                }
                for leg in trip["legs"]
            ],
        }
