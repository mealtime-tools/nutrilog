"""Small contracts for the Google Health write boundary."""

import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
from click.testing import CliRunner
from google.oauth2.credentials import Credentials
from mealtime_nutrients import CORE_NUTRIENTS, NUTRIENTS

from nutrilog.auth import SCOPES
from nutrilog.cli import _total, app, meal_json
from nutrilog.client import GoogleHealthClient
from nutrilog.models import MealLog, MealType, TimeInterval


def meal(**changes) -> MealLog:
    defaults = {
        "name": "Water",
        "meal_type": MealType.SNACK,
        "interval": TimeInterval.from_start(
            datetime(2026, 8, 22, 12, tzinfo=UTC)
        ),
        "kcal": 0,
    }
    return MealLog(**(defaults | changes))


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


def test_vocabulary_is_the_shared_one() -> None:
    """One shared list of names, accepted whole and extended by none."""
    item = {"name": "Everything"} | dict.fromkeys(reversed(NUTRIENTS), 1)
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(item),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert all(data[name] == 1 for name in NUTRIENTS)
    # However an item spelled them, the output states the shared order.
    assert [name for name in data if name in NUTRIENTS] == list(NUTRIENTS)


def test_payload_routing_follows_the_shared_mapping() -> None:
    """Dedicated objects for kcal, carbs and fat; protein is an array entry."""
    log = meal(kcal=100, protein=10, fat=5, carbs=20, nutrients={"fiber": 3})

    payload = log.to_api_payload()["nutritionLog"]

    assert payload["energy"] == {"kcal": 100}
    assert payload["totalCarbohydrate"] == {"grams": 20}
    assert payload["totalFat"] == {"grams": 5}
    assert payload["nutrients"] == [
        {"nutrient": "DIETARY_FIBER", "quantity": {"grams": 3}},
        {"nutrient": "PROTEIN", "quantity": {"grams": 10}},
    ]

    restored = MealLog.from_api_payload(log.to_api_payload())

    assert restored.protein == 10
    assert restored.nutrients == {"fiber": 3}


def test_written_spellings_reach_their_wire_name() -> None:
    result = CliRunner().invoke(
        app,
        "log Spelled --calories 100 --protein 1 --fat 1 --carbs 1"
        " --nutrient saturated-fat=5 --nutrient FIBRE=3"
        " --dry-run --json".split(),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["kcal"] == 100
    assert data["saturated_fat"] == 5
    assert data["fiber"] == 3


def test_api_payload_restores_recorded_utc_offset() -> None:
    offset = timezone(timedelta(hours=10))
    original = meal(
        interval=TimeInterval.from_start(
            datetime(2026, 8, 22, 21, 30, tzinfo=offset)
        )
    )
    payload = original.to_api_payload()
    interval = payload["nutritionLog"]["interval"]
    interval["startTime"] = "2026-08-22T11:30:00+00:00"
    interval["endTime"] = "2026-08-22T11:31:00+00:00"

    restored = MealLog.from_api_payload(payload)

    assert restored.interval.start.isoformat() == "2026-08-22T21:30:00+10:00"
    assert restored.interval.end.isoformat() == "2026-08-22T21:31:00+10:00"


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
    assert "sodium" not in data
    assert "nutrients" not in data


def test_item_carries_the_core_macros_and_nothing_unstated() -> None:
    """Absence and null mean the same, so an unstated nutrient is omitted."""
    item = {"name": "Water", "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(item),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert all(data[name] == 0 for name in CORE_NUTRIENTS)
    assert [n for n in NUTRIENTS if n in data] == list(CORE_NUTRIENTS)


def test_item_carries_a_stated_nutrient_only() -> None:
    item = BARE_ITEM | {"fiber": 0, "saturated_fat": 2.1}
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(item),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["fiber"] == 0
    assert data["saturated_fat"] == 2.1
    assert [n for n in NUTRIENTS if n in data] == [
        *CORE_NUTRIENTS,
        "fiber",
        "saturated_fat",
    ]


def test_legacy_core_macro_renders_as_zero() -> None:
    """Google Health can return an explicit zero as absent; only the four."""
    values = meal_json(meal(kcal=0, protein=None, fat=None, carbs=None))

    assert [values[name] for name in CORE_NUTRIENTS] == [0, 0, 0, 0]


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


BARE_ITEM = {"name": "Typo", "kcal": 100, "protein": 1, "fat": 1, "carbs": 1}
TYPO_ITEM = BARE_ITEM | {"saturatd_fat": 5}


def test_bare_object_rejects_an_unknown_key_by_name() -> None:
    """A bare object is hand-authored, so a misspelling is a mistake."""
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(TYPO_ITEM),
    )

    assert result.exit_code == 1, result.output
    assert "saturatd_fat" in result.output


def test_bare_object_of_known_keys_logs() -> None:
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(BARE_ITEM | {"saturated_fat": 5}),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["saturated_fat"] == 5


def test_envelope_drops_an_unknown_key_in_silence() -> None:
    """The same typo from a tool is that tool's field, not a mistake here."""
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps({"ok": True, "data": TYPO_ITEM}),
    )

    assert result.exit_code == 0, result.output
    assert "saturatd_fat" not in json.loads(result.output)["data"]


# Verbatim `--json` payloads, so the keys these tools wrap an item in stay pipeable.
SIBLING_ENVELOPES = {
    "pantry": {
        "found": True,
        "source": "afcd",
        "id": "F005580",
        "product": {
            "id": "F005580",
            "name": "Milk, cow, canned, evaporated, reduced fat (~2%)",
            "title": "Milk, cow, canned, evaporated, reduced fat (~2%)",
            "kcal": 90.8,
            "kj": 380.0,
            "protein": 7.8,
            "fat": 2.1,
            "carbs": 10.6,
            "fiber": 0,
            "sodium": None,
            "sugar": 10.6,
            "grams": 100,
            "source": "afcd",
        },
    },
    "eatout": {
        "generated_at": "2026-08-21T00:00:00.000Z",
        "count": 1,
        "candidates": [
            {
                "kind": "meal",
                "id": "cali-press-the-shredder-smoothie-regular",
                "name": "Cali Press - The Shredder Smoothie (Regular)",
                "kcal": 356,
                "protein": 31.8,
                "fat": 14.4,
                "carbs": 23.2,
                "fiber": None,
                "sodium": None,
                "sugar": None,
                "complete": True,
                "detail": {"restaurant": "Cali Press", "vegan": True},
            }
        ],
        "unverifiable": [],
    },
    "recipes": {
        "name": "Bean salad",
        "servings": 1,
        "tags": [],
        "notes": "",
        "grams": 350,
        "ingredients": [],
        "complete": True,
        "unresolved": [],
        "kcal": 420,
        "protein": 25,
        "fat": 12,
        "carbs": 48,
        "fiber": 9,
        "sodium": 0.4,
        "sugar": 6,
        "path": "/tmp/recipes/bean-salad.yaml",
    },
}


def test_sibling_envelopes_still_pipe_cleanly() -> None:
    for tool, data in SIBLING_ENVELOPES.items():
        result = CliRunner().invoke(
            app,
            ["log", "--input", "-", "--dry-run", "--json"],
            input=json.dumps({"ok": True, "data": data}),
        )

        assert result.exit_code == 0, f"{tool}: {result.output}"
        assert json.loads(result.output)["data"]["kcal"] > 0, tool


def test_envelope_tolerates_a_field_this_version_never_saw() -> None:
    """The tools ship on their own schedules; a new field is not an error."""
    product = SIBLING_ENVELOPES["pantry"]["product"] | {"confidence": "high"}
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(
            {"ok": True, "data": {"found": True, "product": product}}
        ),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["kcal"] > 0


def test_payload_without_an_item_reports_the_missing_macros() -> None:
    """A Pantry miss keeps `product` null. The envelope makes it tool
    output, so the leftover wrapper keys are dropped, not reported."""
    result = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(
            {
                "ok": True,
                "data": {
                    "found": False,
                    "source": "afcd",
                    "id": "NOSUCH",
                    "product": None,
                },
            }
        ),
    )

    assert result.exit_code == 1
    assert "need kcal, protein, fat, and carbs" in result.output


def test_carbohydrate_is_declared_once() -> None:
    """`carbs` owns `totalCarbohydrate`, so the enum spelling is not a key."""
    item = {"name": "Twice", "kcal": 100, "protein": 1, "fat": 1, "carbs": 25}

    bare = CliRunner().invoke(
        app,
        ["log", "--input", "-", "--dry-run", "--json"],
        input=json.dumps(item | {"carbohydrates": 25}),
    )
    flag = CliRunner().invoke(
        app,
        "log Twice --kcal 100 --protein 1 --fat 1 --carbs 25"
        " --nutrient carbohydrates=25 --dry-run --json".split(),
    )

    assert bare.exit_code == 1, bare.output
    assert "carbohydrates" in bare.output
    assert flag.exit_code == 1, flag.output
    assert "carbohydrates" in flag.output


def test_read_back_never_adds_a_second_carbohydrate() -> None:
    """CARBOHYDRATES is unreachable, so a foreign entry is not read as one."""
    payload = meal(carbs=25).to_api_payload()
    payload["nutritionLog"]["nutrients"] = [
        {"nutrient": "CARBOHYDRATES", "quantity": {"grams": 25}}
    ]

    restored = MealLog.from_api_payload(payload)

    assert restored.carbs == 25
    assert restored.nutrients == {}


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


def test_client_filters_food_log_with_utc_range() -> None:
    current = meal(
        interval=TimeInterval.from_start(datetime(2026, 8, 16, 12, tzinfo=UTC))
    )
    outside = meal(
        name="Coffee",
        interval=TimeInterval.from_start(datetime(2026, 8, 18, 0, tzinfo=UTC)),
    )
    start = datetime(2026, 8, 16, tzinfo=UTC)
    end = datetime(2026, 8, 18, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "filter" not in request.url.params
        return httpx.Response(
            200,
            json={
                "dataPoints": [
                    outside.to_api_payload(),
                    current.to_api_payload(),
                ]
            },
        )

    client = GoogleHealthClient(
        credentials=Credentials(token="token"),
        transport=httpx.MockTransport(handler),
    )

    assert [entry.name for entry in client.history(start, end)] == ["Water"]


def test_history_command_accepts_dates_and_datetimes(monkeypatch) -> None:
    class Client:
        interval: tuple[datetime, datetime] | None = None

        def history(self, start: datetime, end: datetime) -> list[MealLog]:
            Client.interval = (start, end)
            return []

    monkeypatch.setattr("nutrilog.cli.GoogleHealthClient", Client)
    runner = CliRunner()

    result = runner.invoke(
        app, ["history", "2026-08-16", "2026-08-17", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert Client.interval == (
        datetime(2026, 8, 16).astimezone().astimezone(UTC),
        datetime(2026, 8, 18).astimezone().astimezone(UTC),
    )

    result = runner.invoke(
        app,
        [
            "history",
            "2026-08-16T20:00:00+10:00",
            "2026-08-17T01:00:00+10:00",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert Client.interval == (
        datetime(2026, 8, 16, 10, tzinfo=UTC),
        datetime(2026, 8, 16, 15, tzinfo=UTC),
    )


def test_duplicate_never_deletes_and_delete_is_separate(monkeypatch) -> None:
    source = meal(
        id="old",
        name="Ginger beer",
        protein=None,
        fat=0,
        carbs=47,
    )

    class Client:
        saved: MealLog | None = None
        deleted: str | None = None

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
    assert Client.saved is not None
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
    assert Client.saved is not None
    assert Client.saved.interval.start.isoformat() == (
        "2026-08-22T22:30:00+10:00"
    )
    assert Client.deleted is None

    result = CliRunner().invoke(app, ["delete", "old", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert Client.deleted == "old"


def test_history_totals_cover_the_core_and_stated_nutrients(
    monkeypatch,
) -> None:
    class Client:
        def history(self, start: datetime, end: datetime) -> list[MealLog]:
            return [
                meal(kcal=100, protein=10, fat=1, carbs=5),
                meal(
                    kcal=200,
                    protein=20,
                    fat=2,
                    carbs=6,
                    nutrients={"sugar": 3},
                ),
            ]

    monkeypatch.setattr("nutrilog.cli.GoogleHealthClient", Client)
    result = CliRunner().invoke(app, ["history", "--json"])

    assert result.exit_code == 0, result.output
    totals = json.loads(result.output)["data"]["totals"]
    assert totals == {
        "kcal": 300,
        "protein": 30,
        "fat": 3,
        "carbs": 11,
        "sugar": 3,
    }


def test_history_totals_omit_a_nutrient_no_entry_states(monkeypatch) -> None:
    class Client:
        def history(self, start: datetime, end: datetime) -> list[MealLog]:
            return [meal(kcal=100, protein=10, fat=1, carbs=5)]

    monkeypatch.setattr("nutrilog.cli.GoogleHealthClient", Client)
    result = CliRunner().invoke(app, ["history", "--json"])

    assert result.exit_code == 0, result.output
    totals = json.loads(result.output)["data"]["totals"]
    assert set(totals) == set(CORE_NUTRIENTS)


def test_totals_sum_reported_values_and_ignore_null() -> None:
    meals = [
        {"protein": 10.0},
        {"protein": None},
        {"protein": 5.0},
    ]

    assert _total(meals, "protein") == 15.0
    assert _total([{"protein": None}], "protein") is None
    assert _total([], "protein") is None
