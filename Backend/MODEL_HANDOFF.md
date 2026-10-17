# Risk response and ML handoff

The rule-based assessment and the teammate's trained ML hazard index are separate.
No rule level is converted to a numeric ML score. A real model is still required;
when none is connected, `ml_assessment.availability` is `unavailable` and its score
is null. The backend does not train a model or claim that the screening rules satisfy
the challenge's ML requirement.

## Temporal risk fields

`GET /api/sites/{site_id}/risk` returns:

- `current_risk`: latest model conditions, completed historical rainfall, and
  alerts applicable at the actual assessment time. `time` and `alerts_evaluated_at`
  record that time; `weather_observed_at` preserves the weather observation time,
  and `rainfall_as_of` records the completed rainfall window. Recent wildfire reports and wildlife sightings
  provide current context.
- `hourly_predicted_risk`: 48 separate hourly rule-based assessments, with
  offset-aware timestamps. Missing hours/measurements stay unknown.
- `highest_upcoming_risk`: the highest future rule-based level, its timestamp,
  and each hazard's peak and time. It excludes current measurements.
- `ml_assessment`: independently returned ML scores, hazards, model version,
  and target timestamps.

The legacy `overall` and `indicators` fields now alias **current risk only**.
Old v1 snapshots are invalidated, including in `GET /api/risks`, so they cannot
present tomorrow's heat as a current hazard.

Each rule-based frame is explicitly marked `assessment_type: "rule_based"`.
Future rainfall is accumulated only through the hour being assessed. It never
enters current risk or an earlier forecast hour. The preceding 24-hour forecast
window combines available historical precipitation with predicted precipitation;
any missing hour leaves that total unknown. Alert onset/end times are respected.
Wildfire spread and wildlife sightings are not forecast by the configured feeds,
so they remain current context rather than being copied into hourly predictions.

Every lightning card separates:

```json
{
  "coverage": "partial",
  "thunderstorm_forecast": {"availability": "available", "level": "low"},
  "measured_lightning_strikes": {
    "availability": "unknown", "level": "unknown", "count": null
  }
}
```

Thunderstorms or applicable alerts can still raise the screening level. Missing
strike measurements never become zero strikes or complete lightning coverage.
The challenge's strike-distance stop-work/all-clear logic requires a real strike
feed and is not supplied by this forecast proxy.

## Export data to the teammate

Call **`GET /api/sites/{site_id}/ml-input`**. The JSON includes:

- `request_id`: identifies this exact input snapshot; echo it in the result.
- `schema_version`: `site-risk-features-v1`.
- `site`: ID and coordinates.
- `generated_at`: when this input snapshot was assessed.
- `weather_data`: current conditions, complete hourly forecasts, units,
  recent hourly precipitation, and recent rainfall totals.
- `source_availability`: source status and retrieval times.
- `samples`: current time followed by 48 forecast times, each with `id`, `time`,
  `time_scope`, and raw numeric `features`. `feature_units` documents their units.

Features include gust, apparent temperature, preceding-hour/24-hour precipitation,
soil water content, weather code, nearest reported fire distance, wildlife count,
and measured strike count. Null means unknown/unavailable, not zero. Measured
strike count is always null until a strike feed is connected. Missing nearby-fire
distance is also null. Do not interpret wildlife count as an injury-risk label.

The model owns its learned scoring, preprocessing, and missing-value handling.
It can use `weather_data` for additional forecast fields. The supplied rule levels
are not part of the exported sample features.

## Return model results

Post to **`POST /api/sites/{site_id}/ml-output`**:

```json
{
  "request_id": "COPY_FROM_ML_INPUT",
  "model_version": "planit-model-v1",
  "predictions": [
    {
      "time": "2026-10-18T12:00:00-04:00",
      "score": 8.7,
      "construction_hazards": [
        "Heat stress during outdoor work",
        "Vehicle traction loss on wet ground"
      ]
    }
  ]
}
```

This is a **format example**, not an actual model prediction. Use timestamps from
the exported samples and scores produced by the trained model. `model_id` is
optional. `model_version`, timezone-aware `time`, `score`, and
`construction_hazards` are required. Scores must already be finite numbers from
0 to 10; out-of-range scores are rejected rather than rescaled or clamped.

Send one or several predictions (up to 49). Predictions arriving separately are
merged only when their input request, model ID, and version match. Scores for a
new model version replace the previous version's result. The response preserves
the score and hazard descriptions and contains:

- `current_score` and `current`: only the prediction for the current exported time.
- `hourly_scores`: scores/hazards for their specific forecast timestamps; unscored
  hours have null scores and `availability: "unknown"`.
- `highest_upcoming`: the largest returned future ML score with its time/hazards.
- `model_version`, `availability`, `scored_samples`, and `expected_samples`.

Results are persisted in SQLite alongside the input assessment. Returning a
future score never fills `current_score`. A refreshed input invalidates the old
request ID; a delayed result for that old input returns 409. Re-export and re-score
the new input. Invalid numeric/time formats return 422; mismatched target times
or request IDs return 409. The frontend should also respect the snapshot's stale
flag and target times.

If refresh cannot obtain replacement ML predictions (including when no inference
URL is configured), previous scores are retained with their **original** target
times, model version, and `input_generated_at`. Their `ml_assessment.stale` flag
becomes true, and `latest_attempt` explains why no replacement arrived. A retained
`current_score` describes the old `current.time`, not the new assessment time.
New matching model results replace those stale predictions and clear the flag.
The portfolio overview includes the ML stale flag separately from rule-based
assessment freshness. Retention is transactional so a submission arriving during
provider requests is read before the refreshed assessment is saved.

## Optional automatic inference

When your teammate hosts an inference endpoint, set `PLANIT_ML_URL` before
starting the backend. The backend POSTs the same export JSON to that URL during
risk refresh and expects the same model-result JSON above. Optional
`PLANIT_ML_TOKEN` sends a bearer token. `PLANIT_ML_MODEL_VERSION` can invalidate
cached assessments when the deployed model changes. The actual returned model
version is retained in the result.

A failed model request leaves the rule-based assessment available and reports
ML as unavailable. The export/import endpoints work without a hosted inference
service, which supports teammate development and manual handoff.

## Refresh all monitored sites

`POST /api/risks/refresh` now refreshes **all** saved sites, including frontend
creations and the demo. Optional `limit` (1–100) and `offset` support pagination;
neither filters by EIA ID. The CLI also refreshes every site:

```powershell
.\.venv\Scripts\python.exe -B scripts/refresh_risks.py
```

Regression checks:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```
