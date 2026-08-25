"""OAuth and one explicit Google Health write command."""

import json
import math
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TextIO

import click
from agentcli import (
    JsonAwareGroup,
    RemoteError,
    UsageError,
    emit,
    json_option,
    skill_group,
)
from mealtime_nutrients import (
    CORE_NUTRIENTS,
    NUTRIENTS,
    OPTIONAL_NUTRIENTS,
    UNREACHABLE_NUTRIENT_TYPES,
)

from nutrilog import __version__, auth
from nutrilog.client import GoogleHealthClient, GoogleHealthError
from nutrilog.models import MealLog, MealType, TimeInterval

ITEM_FIELDS = ("name", "meal_type", "time", "grams")
# Every key an item may state, ordered so output follows the shared order.
INPUT_FIELDS = (*ITEM_FIELDS, *NUTRIENTS)
# Written spellings that are not the wire name.
NUTRIENT_ALIASES = {"calories": "kcal", "fibre": "fiber"}
NUTRIENT_ARGUMENT = re.compile(r"^([^=]+)=([^=]+)$")


def _nutrient_name(value: str) -> str:
    """A written nutrient spelling reduced to its wire name."""
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    return NUTRIENT_ALIASES.get(key, key)


def _number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise UsageError(f"{field} must be a non-negative number or null")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise UsageError(
            f"{field} must be a non-negative number or null"
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise UsageError(f"{field} must be a non-negative number or null")
    return number


def _time(value: Any) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise UsageError("time must be an ISO 8601 date-time") from exc
    return parsed if parsed.tzinfo else parsed.astimezone()


def _input(stream: TextIO | None) -> dict[str, Any]:
    if stream is None:
        return {}
    try:
        value = json.load(stream)
    except json.JSONDecodeError as exc:
        raise UsageError(f"input is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise UsageError("input must contain one JSON object")

    # Unwrap a JSON envelope or `product` wrapper, so output can be piped in.
    piped = "ok" in value
    if isinstance(value.get("data"), dict):
        value, piped = value["data"], True
    candidates = value.get("candidates")
    if isinstance(candidates, list) and len(candidates) == 1:
        value, piped = candidates[0], True
    if isinstance(value.get("product"), dict):
        value, piped = value["product"], True

    # Only a bare object is hand-authored, so only it reports an unknown key.
    unknown = sorted(set(value) - set(INPUT_FIELDS))
    if unknown and not piped:
        label = "key" if len(unknown) == 1 else "keys"
        raise UsageError(f"unknown input {label}: {', '.join(unknown)}")
    return {key: value[key] for key in INPUT_FIELDS if key in value}


def _nutrients(
    input_data: dict[str, Any], arguments: tuple[str, ...]
) -> tuple[dict[str, float | None], dict[str, float]]:
    combined = {
        key: value for key, value in input_data.items() if key in NUTRIENTS
    }
    for argument in arguments:
        match = NUTRIENT_ARGUMENT.fullmatch(argument.strip())
        if not match:
            raise UsageError("nutrients use NAME=GRAMS")
        combined[match.group(1).strip()] = match.group(2).strip()

    core: dict[str, float | None] = {}
    result: dict[str, float] = {}
    for name, value in combined.items():
        key = _nutrient_name(str(name))
        # Carbohydrate is `carbs`; a second spelling would declare it twice.
        if key.upper() in UNREACHABLE_NUTRIENT_TYPES:
            raise UsageError(f"use carbs, not {name}")
        if key in CORE_NUTRIENTS:
            core[key] = _number(value, key)
            continue
        if key not in NUTRIENTS:
            raise UsageError(f"unknown nutrient: {name}")
        number = _number(value, str(name))
        if number is not None:
            result[key] = number
    return core, result


def _value(flag: Any, input_data: dict[str, Any], *names: str) -> Any:
    if flag is not None:
        return flag
    for name in names:
        if name in input_data:
            return input_data[name]
    return None


def build_meal(
    *,
    name: str | None,
    input_data: dict[str, Any],
    kcal: float | None,
    protein: float | None,
    fat: float | None,
    carbs: float | None,
    grams: float | None,
    nutrient_args: tuple[str, ...],
    meal_type: str | None,
    consumed_at: str | None,
) -> MealLog:
    core, nutrients = _nutrients(input_data, nutrient_args)
    supplied = {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs}
    values = {
        key: _number(supplied[key], key)
        if supplied[key] is not None
        else core.get(key)
        for key in CORE_NUTRIENTS
    }
    missing = [key for key, value in values.items() if value is None]
    if missing:
        raise UsageError("new entries need kcal, protein, fat, and carbs")

    resolved_type = str(_value(meal_type, input_data, "meal_type") or "")
    resolved_grams = _number(_value(grams, input_data, "grams"), "grams")
    if resolved_grams == 0:
        raise UsageError("grams must be positive")

    return MealLog(
        name=str(_value(name, input_data, "name") or "Meal"),
        meal_type=MealType.from_string(resolved_type),
        interval=TimeInterval.from_start(
            _time(_value(consumed_at, input_data, "time"))
        ),
        kcal=values["kcal"],
        protein=values["protein"],
        fat=values["fat"],
        carbs=values["carbs"],
        grams=resolved_grams,
        nutrients=nutrients,
    )


def meal_json(meal: MealLog) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": meal.id,
        "name": meal.name,
        "meal_type": meal.meal_type.value,
        "time": meal.interval.start.isoformat(),
    }
    # Legacy Google entries may omit a core macro; render those as zero.
    values.update({name: getattr(meal, name) or 0 for name in CORE_NUTRIENTS})
    # Every other nutrient appears only where the meal states a figure.
    values.update(meal.nutrients)
    if meal.grams is not None:
        values["grams"] = meal.grams
    return values


def _human(data: dict[str, Any]) -> list[str]:
    def value(key: str) -> str:
        value = data[key]
        return "?" if value is None else str(value)

    return [
        f"{data['name']} ({data['meal_type']})",
        "  ".join(f"{key} {value(key)}" for key in CORE_NUTRIENTS),
        data["time"],
    ]


def _total(meals: list[dict[str, Any]], key: str) -> float | None:
    values = [meal[key] for meal in meals if meal.get(key) is not None]
    return sum(values) if values else None


def _history_human(data: dict[str, Any]) -> list[str]:
    if not data["meals"]:
        return ["No food logged."]
    lines = []
    for meal in data["meals"]:
        consumed = datetime.fromisoformat(meal["time"]).strftime(
            "%Y-%m-%d %H:%M"
        )
        macros = "  ".join(f"{key} {meal[key]}" for key in CORE_NUTRIENTS)
        lines.append(f"{consumed}  {meal['name']}  {macros}")
    summary = data["totals"]
    lines.append(
        "Total  "
        + "  ".join(
            f"{key} {summary[key] if summary[key] is not None else '?'}"
            for key in CORE_NUTRIENTS
        )
    )
    return lines


def _history_value(value: str | None) -> date | datetime:
    today = datetime.now().astimezone().date()
    if value is None or value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise UsageError(f"invalid date or datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise UsageError("history datetimes must include a UTC offset")
    return parsed


def _utc_bound(value: date | datetime, *, end: bool = False) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if end:
        value += timedelta(days=1)
    return datetime.combine(value, time.min).astimezone().astimezone(UTC)


@click.group(cls=JsonAwareGroup)
@click.version_option(__version__)
def app() -> None:
    """Log explicit nutrient data to Google Health."""


@app.command("log")
@click.argument("name", required=False)
@click.option(
    "--input",
    "input_file",
    type=click.File("r", encoding="utf-8"),
    help="JSON object from PATH, or '-' for stdin.",
)
@click.option("--kcal", "--calories", type=float)
@click.option("--protein", type=float)
@click.option("--fat", type=float)
@click.option("--carbs", type=float)
@click.option("--grams", type=click.FloatRange(min=0, min_open=True))
@click.option("--nutrient", multiple=True, help="NAME=GRAMS; repeatable.")
@click.option("--meal", "meal_type")
@click.option("--time", "consumed_at", help="ISO 8601 date-time.")
@click.option("--dry-run", is_flag=True)
@json_option
def log_command(
    name: str | None,
    input_file: TextIO | None,
    kcal: float | None,
    protein: float | None,
    fat: float | None,
    carbs: float | None,
    grams: float | None,
    nutrient: tuple[str, ...],
    meal_type: str | None,
    consumed_at: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Log one meal. Explicit flags override fields in INPUT."""
    meal = build_meal(
        name=name,
        input_data=_input(input_file),
        kcal=kcal,
        protein=protein,
        fat=fat,
        carbs=carbs,
        grams=grams,
        nutrient_args=nutrient,
        meal_type=meal_type,
        consumed_at=consumed_at,
    )
    if not dry_run:
        try:
            meal = GoogleHealthClient().log_meal(meal)
        except GoogleHealthError as exc:
            raise RemoteError(str(exc)) from exc
    emit(meal_json(meal), json_output=json_output, human=_human)


@app.command("duplicate")
@click.argument("point_id")
@click.option("--name")
@click.option(
    "--input",
    "input_file",
    type=click.File("r", encoding="utf-8"),
    help="JSON overrides from PATH, or '-' for stdin.",
)
@click.option("--kcal", "--calories", type=float)
@click.option("--protein", type=float)
@click.option("--fat", type=float)
@click.option("--carbs", type=float)
@click.option("--grams", type=click.FloatRange(min=0, min_open=True))
@click.option("--nutrient", multiple=True, help="NAME=GRAMS; repeatable.")
@click.option("--meal", "meal_type")
@click.option("--time", "consumed_at", help="ISO 8601 date-time.")
@click.option("--dry-run", is_flag=True)
@json_option
def duplicate_command(
    point_id: str,
    name: str | None,
    input_file: TextIO | None,
    kcal: float | None,
    protein: float | None,
    fat: float | None,
    carbs: float | None,
    grams: float | None,
    nutrient: tuple[str, ...],
    meal_type: str | None,
    consumed_at: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Create another meal, optionally overriding fields."""
    client = GoogleHealthClient()
    try:
        source = client.get_meal(point_id)
        values = meal_json(source)
        input_values = _input(input_file)
        values.update(input_values)
        duplicate = build_meal(
            name=name,
            input_data=values,
            kcal=kcal,
            protein=protein,
            fat=fat,
            carbs=carbs,
            grams=grams,
            nutrient_args=nutrient,
            meal_type=meal_type,
            consumed_at=consumed_at,
        )
        if not dry_run:
            duplicate = client.log_meal(duplicate)
    except GoogleHealthError as exc:
        raise RemoteError(str(exc)) from exc
    emit(meal_json(duplicate), json_output=json_output, human=_human)


@app.command("delete")
@click.argument("point_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@json_option
def delete_command(point_id: str, yes: bool, json_output: bool) -> None:
    """Delete one meal explicitly."""
    if json_output and not yes:
        raise UsageError("delete --json needs --yes")
    if not yes and not click.confirm(f"Delete meal {point_id}?"):
        click.echo("Deletion cancelled.")
        return
    try:
        GoogleHealthClient().delete_meal(point_id)
    except GoogleHealthError as exc:
        raise RemoteError(str(exc)) from exc
    emit(
        {"id": point_id, "deleted": True},
        json_output=json_output,
        human=lambda data: [f"Deleted {data['id']}."],
    )


@app.command("history")
@click.argument("start", required=False)
@click.argument("end", required=False)
@json_option
def history_command(
    start: str | None, end: str | None, json_output: bool
) -> None:
    """Show logs in a date or time range.

    Values may be dates or offset-aware ISO datetimes. With no values, show
    today; one value must be a date and selects that local calendar day. An
    end date is inclusive; an end datetime is exclusive.
    """
    start_value = _history_value(start)
    if end is None and isinstance(start_value, datetime):
        raise UsageError("a history datetime needs an end datetime or date")
    end_value = _history_value(end) if end else start_value
    start_time = _utc_bound(start_value)
    end_time = _utc_bound(end_value, end=True)
    if end_time <= start_time:
        raise UsageError("history end must follow start")
    try:
        meals = [
            meal_json(meal)
            for meal in GoogleHealthClient().history(start_time, end_time)
        ]
    except GoogleHealthError as exc:
        raise RemoteError(str(exc)) from exc
    # A nutrient is totalled only where an entry states it; the core always.
    stated = {name for meal in meals for name in meal}
    optional = [name for name in OPTIONAL_NUTRIENTS if name in stated]
    summary = {key: _total(meals, key) for key in (*CORE_NUTRIENTS, *optional)}
    emit(
        {"count": len(meals), "totals": summary, "meals": meals},
        json_output=json_output,
        human=_history_human,
    )


@click.group("auth")
def auth_app() -> None:
    """Manage Google OAuth credentials."""


@auth_app.command("login")
@click.option("--secrets", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--port", default=0, type=int)
@click.option("--remote", is_flag=True)
def auth_login(secrets: Path | None, port: int, remote: bool) -> None:
    """Authorize the Google Health write scope."""
    if remote or auth.is_headless_or_ssh():
        auth.login_remote(client_config_path=secrets)
    else:
        auth.login(client_config_path=secrets, port=port)
    click.echo("Authenticated.")


@auth_app.command("status")
@json_option
def auth_status(json_output: bool) -> None:
    """Show whether usable credentials are stored."""
    emit(
        auth.get_auth_status(),
        json_output=json_output,
        human=lambda data: [
            "authenticated" if data["authenticated"] else "not authenticated"
        ],
    )


@auth_app.command("logout")
def auth_logout() -> None:
    """Delete the stored OAuth token."""
    click.echo("Logged out." if auth.logout() else "No stored token.")


for command in (auth_app, skill_group(name="nutrilog", package="nutrilog")):
    app.add_command(command)
