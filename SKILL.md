---
name: nutrilog
description: Log explicit nutrient data to Google Health and view today's food log.
---

# Nutrilog

Use `nutrilog log` only after the user has authorized the write. Preview first
when requested:

```console
nutrilog log --input - --dry-run --json
nutrilog log --input - --json
nutrilog history --json
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

Daily totals sum reported values and ignore `null`; they are `null` only when
no entry reports that nutrient.
