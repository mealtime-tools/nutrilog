---
name: nutrilog
description: Log explicit nutrient data to Google Health and view food history.
---

# Nutrilog

Use `nutrilog log` only after the user has authorized the write. Preview first
when requested:

```console
nutrilog log --input - --dry-run --json
nutrilog log --input - --json
nutrilog history --json
nutrilog history yesterday --json
nutrilog history 2026-08-17 2026-08-23 --json
nutrilog history '2026-08-22T20:00:00+10:00' '2026-08-23T01:00:00+10:00' --json
nutrilog duplicate POINT_ID --input - --json
nutrilog delete POINT_ID --yes --json
```

Pass one flat JSON object with `name`, `meal_type`, `time`, optional `grams`,
and nutrient fields.
Every new log needs kcal, protein, fat, and carbs. Nutrilog renders missing
legacy Google core macros as zero for interoperability; no other missing
nutrient is inferred.
`--input -` accepts single Pantry, Eatout, Recipes, and Nutrilog JSON items.
`grams`, when present, is preserved in Google Health.

`duplicate` accepts the same field overrides as `log` and never deletes the
source. Correct in two explicit steps: duplicate, inspect the saved entry, then
use `delete --yes` only when the user authorizes deleting the source.

History totals sum reported values and ignore `null`; they are `null` only when
no entry reports that nutrient.
History accepts local dates or offset-aware ISO datetimes. Dates use the
device's timezone and become UTC bounds; an end date is inclusive while an end
datetime is exclusive. Entry selection compares timestamps only in UTC.
