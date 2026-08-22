"""The two Google Health requests Nutrilog makes."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

import httpx
from google.oauth2.credentials import Credentials

from nutrilog.auth import get_credentials
from nutrilog.models import MealLog

API_BASE_URL = "https://health.googleapis.com/v4"


class GoogleHealthError(Exception):
    pass


class GoogleHealthClient:
    def __init__(
        self,
        credentials: Credentials | None = None,
        base_url: str = API_BASE_URL,
        timeout: float = 15,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        credentials = self.credentials or get_credentials()
        if not credentials or not credentials.token:
            raise GoogleHealthError(
                "Not authenticated. Run 'nutrilog auth login'."
            )
        return {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

    @contextmanager
    def _client(self) -> Generator[httpx.Client]:
        try:
            with httpx.Client(
                timeout=self.timeout, transport=self.transport
            ) as client:
                yield client
        except httpx.HTTPStatusError as exc:
            message = _error_message(exc.response)
            raise GoogleHealthError(
                f"Google Health returned {exc.response.status_code}: {message}"
            ) from exc
        except httpx.RequestError as exc:
            raise GoogleHealthError(
                f"Google Health request failed: {exc}"
            ) from exc

    def log_meal(self, meal: MealLog) -> MealLog:
        url = f"{self.base_url}/users/me/dataTypes/nutrition-log/dataPoints"
        with self._client() as client:
            response = client.post(
                url, json=meal.to_api_payload(), headers=self._headers()
            )
            response.raise_for_status()
            data = response.json()
            saved = MealLog.from_api_payload(data.get("response", data))
            if saved.grams is None:
                saved.grams = meal.grams
            return saved

    def get_meal(self, point_id: str) -> MealLog:
        url = (
            f"{self.base_url}/users/me/dataTypes/nutrition-log/dataPoints/"
            f"{point_id}"
        )
        with self._client() as client:
            response = client.get(url, headers=self._headers())
            response.raise_for_status()
            return MealLog.from_api_payload(response.json())

    def delete_meal(self, point_id: str) -> None:
        url = (
            f"{self.base_url}/users/me/dataTypes/"
            "nutrition-log/dataPoints:batchDelete"
        )
        name = f"users/me/dataTypes/nutrition-log/dataPoints/{point_id}"
        with self._client() as client:
            response = client.post(
                url,
                json={"names": [name]},
                headers=self._headers(),
            )
            response.raise_for_status()

    def today(self) -> list[MealLog]:
        """Read nutrition logs whose start time is today locally."""
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        url = f"{self.base_url}/users/me/dataTypes/nutrition-log/dataPoints"
        with self._client() as client:
            response = client.get(
                url,
                params={"pageSize": 10000},
                headers=self._headers(),
            )
            response.raise_for_status()
            points = response.json().get("dataPoints") or []

        meals = [MealLog.from_api_payload(point) for point in points]
        return [
            meal
            for meal in meals
            if start <= meal.interval.start.astimezone() <= end
        ]


def _error_message(response: httpx.Response) -> str:
    try:
        return str(
            response.json().get("error", {}).get("message") or response.text
        )
    except ValueError:
        return response.text
