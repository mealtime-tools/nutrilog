"""The small nutrition-log shape sent to Google Health."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from typing import Any


class MealType(str, Enum):
    MEAL_TYPE_UNSPECIFIED = "MEAL_TYPE_UNSPECIFIED"
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    SNACK = "SNACK"

    @classmethod
    def from_string(cls, value: str | None) -> "MealType":
        key = (value or "").strip().upper()
        aliases = {
            "B": cls.BREAKFAST,
            "L": cls.LUNCH,
            "D": cls.DINNER,
            "S": cls.SNACK,
        }
        return cls.__members__.get(
            key, aliases.get(key, cls.MEAL_TYPE_UNSPECIFIED)
        )


class NutrientType(str, Enum):
    BIOTIN = "BIOTIN"
    CAFFEINE = "CAFFEINE"
    CALCIUM = "CALCIUM"
    CARBOHYDRATES = "CARBOHYDRATES"
    CHLORIDE = "CHLORIDE"
    CHOLESTEROL = "CHOLESTEROL"
    CHROMIUM = "CHROMIUM"
    COPPER = "COPPER"
    DIETARY_FIBER = "DIETARY_FIBER"
    FOLATE = "FOLATE"
    FOLIC_ACID = "FOLIC_ACID"
    IODINE = "IODINE"
    IRON = "IRON"
    MAGNESIUM = "MAGNESIUM"
    MANGANESE = "MANGANESE"
    MOLYBDENUM = "MOLYBDENUM"
    MONOUNSATURATED_FAT = "MONOUNSATURATED_FAT"
    NIACIN = "NIACIN"
    PANTOTHENIC_ACID = "PANTOTHENIC_ACID"
    PHOSPHORUS = "PHOSPHORUS"
    POLYUNSATURATED_FAT = "POLYUNSATURATED_FAT"
    POTASSIUM = "POTASSIUM"
    PROTEIN = "PROTEIN"
    RIBOFLAVIN = "RIBOFLAVIN"
    SATURATED_FAT = "SATURATED_FAT"
    SELENIUM = "SELENIUM"
    SODIUM = "SODIUM"
    SUGAR = "SUGAR"
    THIAMIN = "THIAMIN"
    TRANS_FAT = "TRANS_FAT"
    UNSATURATED_FAT = "UNSATURATED_FAT"
    VITAMIN_A = "VITAMIN_A"
    VITAMIN_B12 = "VITAMIN_B12"
    VITAMIN_B6 = "VITAMIN_B6"
    VITAMIN_C = "VITAMIN_C"
    VITAMIN_D = "VITAMIN_D"
    VITAMIN_E = "VITAMIN_E"
    VITAMIN_K = "VITAMIN_K"
    ZINC = "ZINC"

    @classmethod
    def from_string(cls, value: str) -> "NutrientType | None":
        key = re.sub(r"[\s-]+", "_", value.strip().upper())
        key = {"FIBER": "DIETARY_FIBER", "FIBRE": "DIETARY_FIBER"}.get(
            key, key
        )
        return cls.__members__.get(key)


def _offset(value: datetime) -> str:
    return f"{int((value.utcoffset() or timedelta()).total_seconds())}s"


@dataclass(frozen=True)
class TimeInterval:
    start: datetime
    end: datetime

    @classmethod
    def from_start(cls, start: datetime) -> "TimeInterval":
        if start.tzinfo is None:
            start = start.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return cls(start=start, end=start + timedelta(minutes=1))

    def as_api(self) -> dict[str, str]:
        return {
            "startTime": self.start.isoformat(),
            "endTime": self.end.isoformat(),
            "startUtcOffset": _offset(self.start),
            "endUtcOffset": _offset(self.end),
        }


@dataclass
class MealLog:
    name: str
    meal_type: MealType
    interval: TimeInterval
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carbs: float | None = None
    grams: float | None = None
    nutrients: dict[NutrientType, float] = field(default_factory=dict)
    id: str | None = None

    def to_api_payload(self) -> dict[str, Any]:
        """Omit unknown fields. Keep explicitly supplied zeros."""
        log: dict[str, Any] = {
            "foodDisplayName": self.name,
            "mealType": self.meal_type.value,
            "interval": self.interval.as_api(),
        }
        if self.kcal is not None:
            log["energy"] = {"kcal": self.kcal}
        if self.carbs is not None:
            log["totalCarbohydrate"] = {"grams": self.carbs}
        if self.fat is not None:
            log["totalFat"] = {"grams": self.fat}
        if self.grams is not None:
            log["serving"] = {
                "amount": self.grams,
                "foodMeasurementUnitDisplayName": "gram",
            }

        nutrients = dict(self.nutrients)
        if self.protein is not None:
            nutrients[NutrientType.PROTEIN] = self.protein
        if nutrients:
            log["nutrients"] = [
                {"nutrient": nutrient.value, "quantity": {"grams": grams}}
                for nutrient, grams in sorted(
                    nutrients.items(), key=lambda item: item[0].value
                )
            ]
        return {"nutritionLog": log}

    @classmethod
    def from_api_payload(cls, data: dict[str, Any]) -> "MealLog":
        log = data.get("nutritionLog", data)
        raw_interval = log.get("interval") or {}
        start = _datetime(
            raw_interval.get("startTime"),
            utc_offset=raw_interval.get("startUtcOffset"),
        )
        end = _datetime(
            raw_interval.get("endTime"),
            start + timedelta(minutes=1),
            raw_interval.get("endUtcOffset"),
        )
        nutrients: dict[NutrientType, float] = {}
        for entry in log.get("nutrients") or []:
            nutrient = NutrientType.from_string(
                str(entry.get("nutrient") or "")
            )
            grams = (entry.get("quantity") or {}).get("grams")
            if nutrient is not None and grams is not None:
                nutrients[nutrient] = float(grams)
        protein = nutrients.pop(NutrientType.PROTEIN, None)
        serving = log.get("serving") or {}
        unit = str(serving.get("foodMeasurementUnitDisplayName") or "")
        grams = (
            _quantity(serving, "amount")
            if unit.casefold() in ("g", "gram", "grams")
            else None
        )
        return cls(
            id=(data.get("name") or data.get("id") or "").split("/")[-1]
            or None,
            name=str(log.get("foodDisplayName") or "Meal"),
            meal_type=MealType.from_string(log.get("mealType")),
            interval=TimeInterval(start=start, end=end),
            kcal=_quantity(log.get("energy"), "kcal"),
            carbs=_quantity(log.get("totalCarbohydrate"), "grams"),
            fat=_quantity(log.get("totalFat"), "grams"),
            protein=protein,
            grams=grams,
            nutrients=nutrients,
        )


def _quantity(container: Any, key: str) -> float | None:
    value = container.get(key) if isinstance(container, dict) else None
    return float(value) if value is not None else None


def _datetime(
    value: Any,
    default: datetime | None = None,
    utc_offset: Any = None,
) -> datetime:
    parsed = (
        datetime.fromisoformat(str(value))
        if value is not None
        else default or datetime.now(UTC)
    )
    offset = _fixed_offset(utc_offset)
    return parsed.astimezone(offset) if offset else parsed


def _fixed_offset(value: Any) -> timezone | None:
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)s", str(value or ""))
    if match is None:
        return None
    try:
        return timezone(timedelta(seconds=float(match.group(1))))
    except ValueError:
        return None
