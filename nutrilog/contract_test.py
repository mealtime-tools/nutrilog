"""Small contracts for the Google Health write boundary."""

import json
from datetime import UTC, datetime

import httpx
from click.testing import CliRunner
from google.oauth2.credentials import Credentials

from nutrilog.auth import SCOPES
from nutrilog.cli import _total, app
from nutrilog.client import GoogleHealthClient
from nutrilog.models import MealLog, MealType, TimeInterval


def meal(**changes) -> MealLog:
    values = {
        "name": "Water",
        "meal_type": MealType.SNACK,
        "interval": TimeInterval.from_start(
            datetime(2026, 8, 22, 12, tzinfo=UTC)
        ),
        "kcal": 0,
    }
    values.update(changes)
    return MealLog(**values)


def test_api_payload_omits_unknowns_and_keeps_explicit_zero() -> None:
    log = meal(grams=90)

    payload = log.to_api_payload()["nutritionLog"]
    assert payload["energy"] == {"kcal": 0}
    assert "totalFat" not in payload
    assert "totalCarbohydrate" not in payload
    assert "nutrients" not in payload
    assert payload["serving"] == {
        "amount": 90,
        "foodMeasurementUnitDisplayName": "gram",
    }


def test_json_input_and_output_are_flat() -> None:
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(
            {
                "name": "Water",
                "kcal": 0,
                "protein": 0,
                "fat": 0,
                "carbs": 0,
                "sodium": None,
            }
        ),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["kcal"] == 0
    assert data["protein"] == 0
    assert data["sodium"] is None
    assert "nutrients" not in data


def test_new_entry_requires_every_core_nutrient() -> None:
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps({"name": "Incomplete", "kcal": 1}),
    )

    assert result.exit_code == 1
    assert "need kcal, protein, fat, and carbs" in result.output


def test_piped_whole_item_keeps_total_nutrients() -> None:
    item = {
        "ok": True,
        "data": {
            "name": "Protein bar",
            "grams": 50,
            "kcal": 200,
            "protein": 20,
            "fat": 5,
            "carbs": 15,
        },
    }
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(item),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["protein"] == 20
    assert json.loads(result.output)["data"]["grams"] == 50


def test_client_posts_one_nutrition_log() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "done": True,
                "response": seen["body"] | {"name": "users/me/dataPoints/123"},
            },
        )

    client = GoogleHealthClient(
        credentials=Credentials(token="token"),
        transport=httpx.MockTransport(handler),
    )
    saved = client.log_meal(meal())

    assert seen["path"].endswith("/nutrition-log/dataPoints")
    assert seen["body"]["nutritionLog"]["energy"] == {"kcal": 0}
    assert saved.id == "123"
    assert SCOPES == [
        "https://www.googleapis.com/auth/googlehealth.nutrition.writeonly",
        "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    ]


def test_client_reads_todays_food_log() -> None:
    current = meal()
    current.interval = TimeInterval.from_start(datetime.now().astimezone())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"dataPoints": [current.to_api_payload()]},
        )

    client = GoogleHealthClient(
        credentials=Credentials(token="token"),
        transport=httpx.MockTransport(handler),
    )

    assert [entry.name for entry in client.today()] == ["Water"]


def test_duplicate_never_deletes_and_delete_is_separate(monkeypatch) -> None:
    source = meal(
        id="old",
        name="Ginger beer",
        protein=None,
        fat=0,
        carbs=47,
    )

    class Client:
        saved = None
        deleted = None

        def get_meal(self, point_id: str) -> MealLog:
            assert point_id == "old"
            return source

        def log_meal(self, value: MealLog) -> MealLog:
            Client.saved = value
            value.id = "new"
            return value

        def delete_meal(self, point_id: str) -> None:
            Client.deleted = point_id

    monkeypatch.setattr("nutrilog.cli.GoogleHealthClient", Client)
    result = CliRunner().invoke(
        app,
        [
            "duplicate",
            "old",
            "--protein",
            "0",
            "--grams",
            "90",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert Client.saved.protein == 0
    assert Client.saved.carbs == 47
    assert Client.saved.grams == 90
    assert Client.saved.interval == source.interval
    assert Client.deleted is None
    assert json.loads(result.output)["data"]["id"] == "new"

    Client.deleted = None
    result = CliRunner().invoke(
        app,
        ["duplicate", "old", "--time", "2026-08-22T22:30:00+10:00"],
    )

    assert result.exit_code == 0, result.output
    assert Client.saved.interval.start.isoformat() == (
        "2026-08-22T22:30:00+10:00"
    )
    assert Client.deleted is None

    result = CliRunner().invoke(app, ["delete", "old", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert Client.deleted == "old"


def test_totals_sum_reported_values_and_ignore_null() -> None:
    meals = [
        {"protein": 10.0},
        {"protein": None},
        {"protein": 5.0},
    ]

    assert _total(meals, "protein") == 15.0
    assert _total([{"protein": None}], "protein") is None
    assert _total([], "protein") is None
