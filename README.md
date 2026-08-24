# Nutrilog

Write explicit nutrient data to Google Health.

```console
nutrilog auth login
nutrilog log "Bean salad" --grams 350 --kcal 420 --protein 25 --fat 12 --carbs 48
nutrilog log --input meal.json
cat meal.json | nutrilog log --input -
nutrilog history
nutrilog history yesterday
nutrilog history 2026-08-17 2026-08-23
nutrilog history '2026-08-22T20:00:00+10:00' '2026-08-23T01:00:00+10:00'
nutrilog duplicate POINT_ID --protein 0
nutrilog delete POINT_ID
```

JSON input is a flat item carrying `name`, `meal_type`, optional `time`,
optional `grams`, and nutrient fields. Omit `time` to use the device's current
local time.
Every new entry needs `kcal`, `protein`, `fat`, and `carbs`; explicit zero is a
valid value. Use `--nutrient NAME=GRAMS` for another nutrient; names come from
`mealtime-nutrients`, the list the mealtime tools share, which holds exactly
one per nutrient. Dietary fibre is `fiber`, and carbohydrate has its own field,
so it is `carbs` and never `carbohydrates`. Explicit flags override the input.
Piped tool output keeps its `{"ok":true,"data":...}` envelope, and a field this
version has not heard of is dropped, so the other tools stay free to add one.
A bare JSON object is read as hand-written instead: an unrecognised key there
is an error, rather than a nutrient quietly left out of the entry.
`grams` is written to Google as a gram serving and survives reads. When
it is absent, the shared format treats the nutrients as a 100 g fallback.

`duplicate` always keeps the source and accepts the same overrides as `log`.
To correct an entry, duplicate it with the correction, inspect the result, then
delete the source explicitly. JSON overrides may use `null` to remove a value.

Output carries `kcal`, `protein`, `fat` and `carbs` always, plus only the
nutrients the entry states; an absent key and a `null` mean the same, while an
explicit zero survives. Missing legacy Google core macros render as zero in
Nutrilog output. Unstated nutrients are omitted from writes. `--dry-run --json`
shows the record without authenticating or writing.

`nutrilog history` reads today by default. Pass one date for that day or two
dates for an inclusive range; dates may be ISO dates, `today`, or `yesterday`.
Dates become UTC bounds using the device's local timezone. Offset-aware ISO
datetimes are exact bounds; the end datetime is exclusive. Entries are then
compared only in UTC. Core macro totals are always present. Every other
nutrient is totalled only when an entry states it, over the entries that state
it, so a total may cover part of the range.

OAuth tokens remain in `~/.config/nutrilog/tokens.json` with mode `0600`.
