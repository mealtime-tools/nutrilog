---
name: nutrilog
description: Log nutrient data to Google Health and inspect food history.
---

# Nutrilog

Use JSON output when consuming commands:

```console
nutrilog log --input - --json
nutrilog history [START] [END] --json
nutrilog duplicate POINT_ID --input - --json
nutrilog delete POINT_ID --yes --json
```

`log` writes to Google Health, so run it only with user authorization. New
entries require `kcal`, `protein`, `fat`, and `carbs`. `--input -` accepts one
flat JSON item from Pantry, Eatout, Recipes, or Nutrilog. Omit `time` unless the
user specified one; Nutrilog uses the device time, so never look up "now".

`history` defaults to today. Bounds accept `today`, `yesterday`, ISO dates, or
offset-aware ISO datetimes. Dates use the device timezone.

`duplicate` creates a copy and never deletes its source. To correct an entry,
duplicate it, inspect the copy, then delete the source only with explicit user
authorization. Missing optional nutrients remain `null`; totals ignore them.
