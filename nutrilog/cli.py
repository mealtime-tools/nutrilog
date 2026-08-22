"""OAuth and one explicit Google Health write command."""

import json
import math
import re
from datetime import datetime
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

from nutrilog import __version__, auth
from nutrilog.client import GoogleHealthClient, GoogleHealthError
from nutrilog.models import MealLog, MealType, NutrientType, TimeInterval

CORE_NUTRIENTS = ("kcal", "protein", "fat", "carbs")
STANDARD_NUTRIENTS = {
    "fiber": NutrientType.DIETARY_FIBER,
    "sodium": NutrientType.SODIUM,
    "sugar": NutrientType.SUGAR,
}
ITEM_FIELDS = {"name", "meal_type", "time", "grams"}
NUTRIENT_FIELDS = {
    "kcal",
    "protein",
    "fat",
    "carbs",
    *STANDARD_NUTRIENTS,
    *(nutrient.value.lower() for nutrient in NutrientType),
}
NUTRIENT_ARGUMENT = re.compile(r"^([^=]+)=([^=]+)$")


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

    # JSON commands emit an envelope; acquisition commands may also wrap the
    # item as `product`. Unwrap both so their output can be piped directly.
    if isinstance(value.get("data"), dict):
        value = value["data"]
    candidates = value.get("candidates")
    if isinstance(candidates, list) and len(candidates) == 1:
        value = candidates[0]
    if isinstance(value.get("product"), dict):
        value = value["product"]

    fields = ITEM_FIELDS | NUTRIENT_FIELDS
    return {key: value[key] for key in fields if key in value}


def _nutrients(
    input_data: dict[str, Any], arguments: tuple[str, ...]
) -> tuple[dict[str, float | None], dict[NutrientType, float]]:
    combined = {
        key: value
        for key, value in input_data.items()
        if key in NUTRIENT_FIELDS
    }
    for argument in arguments:
        match = NUTRIENT_ARGUMENT.fullmatch(argument.strip())
        if not match:
            raise UsageError("nutrients use NAME=GRAMS")
        combined[match.group(1).strip()] = match.group(2).strip()

    core: dict[str, float | None] = {}
    result: dict[NutrientType, float] = {}
    for name, value in combined.items():
        key = str(name).strip().lower()
        if key == "calories":
            key = "kcal"
        if key in CORE_NUTRIENTS:
            core[key] = _number(value, key)
            continue
        nutrient = NutrientType.from_string(str(name))
        if nutrient is None:
            raise UsageError(f"unknown nutrient: {name}")
        number = _number(value, str(name))
        if number is not None:
            result[nutrient] = number
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
    values.update(
        {
            "kcal": meal.kcal or 0,
            "protein": meal.protein or 0,
            "fat": meal.fat or 0,
            "carbs": meal.carbs or 0,
        }
    )
    for name, nutrient in STANDARD_NUTRIENTS.items():
        values[name] = meal.nutrients.get(nutrient)
    values.update(
        {
            nutrient.value.lower(): grams
            for nutrient, grams in meal.nutrients.items()
            if nutrient not in STANDARD_NUTRIENTS.values()
        }
    )
    if meal.grams is not None:
        values["grams"] = meal.grams
    return values


def _human(data: dict[str, Any]) -> list[str]:
    def value(key: str) -> str:
        value = data[key]
        return "?" if value is None else str(value)

    return [
        f"{data['name']} ({data['meal_type']})",
        "  ".join(
            f"{key} {value(key)}"
            for key in ("kcal", "protein", "fat", "carbs")
        ),
        data["time"],
    ]


def _total(meals: list[dict[str, Any]], key: str) -> float | None:
    values = [meal[key] for meal in meals if meal.get(key) is not None]
    return sum(values) if values else None


def _history_human(data: dict[str, Any]) -> list[str]:
    if not data["meals"]:
        return ["No food logged today."]
    lines = []
    for meal in data["meals"]:
        consumed = datetime.fromisoformat(meal["time"]).strftime("%H:%M")
        macros = "  ".join(
            f"{key} {meal[key]}" for key in ("kcal", "protein", "fat", "carbs")
        )
        lines.append(f"{consumed}  {meal['name']}  {macros}")
    summary = data["totals"]
    lines.append(
        "Total  "
        + "  ".join(
            f"{key} {summary[key] if summary[key] is not None else '?'}"
            for key in ("kcal", "protein", "fat", "carbs")
        )
    )
    return lines


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
@json_option
def history_command(json_output: bool) -> None:
    """Show today's Google Health food log."""
    try:
        meals = [meal_json(meal) for meal in GoogleHealthClient().today()]
    except GoogleHealthError as exc:
        raise RemoteError(str(exc)) from exc
    summary = {
        key: _total(meals, key)
        for key in (
            "kcal",
            "protein",
            "fat",
            "carbs",
            "fiber",
            "sodium",
            "sugar",
        )
    }
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
