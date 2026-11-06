# Digital Wellbeing Risk & Deviation Detection System

An Android app that reads **your own phone's usage statistics** — the same data
Android records for its built-in Digital Wellbeing screen — and runs eight
machine-learning modules over them: forecasting, risk classification,
clustering, personal-baseline anomaly detection, a PCA behaviour index,
Bayesian confidence intervals and KNN-based recommendations.

It is an academic prototype. **It is not a medical device and gives no
diagnosis.**

---

## What makes this different from a mock-up

Every number the app displays is computed from a trained model, or refused.
There are no placeholder predictions, no invented accuracy figures and no
hard-coded confidence intervals. Where a model cannot run, the app says which
measurement is missing and what will make it available — see
[Honest refusals](#honest-refusals).

Several findings below are **negative results that were kept rather than
hidden**: the clustering found no real structure, and the PCA risk score failed
its validation and was demoted from the headline as a result. Both are reported
in the app itself.

---

## Stack

| Layer | Technology |
|---|---|
| App | React Native 0.88 · Expo SDK 58 · TypeScript · Expo Router |
| Native data access | Custom Expo module in **Kotlin** (`modules/usage-stats/`) |
| API | Python · FastAPI · Pydantic v2 · SQLAlchemy 2.0 |
| ML | scikit-learn · NumPy · pandas · SciPy |
| Storage | SQLite |

The app talks to a FastAPI backend running on a laptop over the local network
(phone hotspot or shared Wi-Fi). The backend does all model fitting and
inference; the phone collects data and renders.

---

## Repository layout

```
modules/usage-stats/        Kotlin Expo module - reads UsageStatsManager,
                            NotificationListenerService and the call log,
                            and stores each captured day durably on the device
src/
  app/(tabs)/               The 8 screens (Expo Router)
  services/api/             Typed client with backend address auto-discovery
  services/usage/           Device collection, durable capture, background sync
  types/api.ts              Hand-written mirrors of every API response
backend/
  app/api/                  Routers: usage, data, ml, health
  app/ml/
    preprocessing/          Cleaning + the documented feature dictionary
    regression/             train_forecast.py  /  inference.py
    classification/         train_risk.py      /  inference.py
    clustering/             personas from measured centroids
    anomaly_detection/      personal robust baselines
    risk_scoring/           PCA index + Bayesian layer
    recommendations/        KNN neighbour comparison
    artifacts/              Trained models (.joblib) + metrics (.json)
  scripts/                  Training entry points
  tests/                    141 tests
```

**Training code is separate from inference code throughout.** A module named
`train_*.py` fits and saves; the API only ever loads. No API request can
trigger a fit.

---

## Running it

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters: the phone connects over the network, so binding to
localhost only would make the backend unreachable. Allow Python through the
Windows firewall on first run.

Check it at <http://localhost:8000/api/health>. The response includes
`lan_addresses`, the laptop's own addresses on the current network — the app
shows these on its Profile screen, so a changed hotspot IP can be read off the
phone instead of hunted down with `ipconfig`.

### 2. Train the models

```bash
cd backend
.venv/Scripts/python.exe scripts/train_models.py                     # regression + classification
.venv/Scripts/python.exe scripts/train_clusters_and_score_anomalies.py
.venv/Scripts/python.exe scripts/train_risk_score.py
.venv/Scripts/python.exe scripts/train_recommender.py
```

Each script prints its real metrics, including the ones that look bad. To fit
the forecaster on measured days only:

```bash
.venv/Scripts/python.exe scripts/train_models.py --only regression --sources device
```

That refuses unless at least 21 consecutive measured days exist — see
[Current state](#current-state-on-real-data).

### 3. The app

A **development build** is required, not Expo Go: the project contains a custom
native module, and Expo Go cannot load one.

```bash
npx expo run:android      # builds and installs the dev build
npx expo start            # subsequent runs
```

### 4. Grant the three permissions

On first launch the app opens a Permissions screen. All three are granted in
Android Settings rather than by a dialog:

| Permission | Why | Backfill? |
|---|---|---|
| Usage access | Screen time, app sessions, unlocks | ~1 week of history |
| Notification access | Notification counts | **No** — counts start the day you grant it |
| Call log | Calls and durations | Yes |

Then press **Sync** on the Usage Data screen.

---

## The eight ML modules

| | Module | Where | Measured result |
|---|---|---|---|
| **A** | Preprocessing + feature dictionary | `app/ml/preprocessing/` | Every feature documented with unit, origin, valid range and formula; served at `/api/data/feature-dictionary` |
| **B** | Linear / Ridge / Lasso regression | `app/ml/regression/` | Best **Lasso**, RMSE **60.5 min** vs naive-persistence **77.2 min**, R² 0.429 |
| **C** | Seven classifiers | `app/ml/classification/` | Best **Bagging**, CV F1-macro **0.638**, test ROC-AUC **0.802** vs majority baseline **0.383** |
| **D** | K-Means / DBSCAN / BIRCH | `app/ml/clustering/` | Silhouette **0.107** — *no real structure*; KMeans vs BIRCH ARI **0.026** |
| **E** | Personal-baseline anomaly detection | `app/ml/anomaly_detection/` | **3/3 recall** on injected anomalies; median + MAD, not fixed thresholds |
| **F** | PCA risk score (0–100) | `app/ml/risk_scoring/` | **Failed validation** (Spearman ρ 0.18; Medium 53.4 vs High 54.4) — demoted to a behaviour index |
| **G** | Bayesian confidence intervals | `app/ml/risk_scoring/` | Beta-Binomial with Jeffreys prior; autocorrelation correction widens the interval **1.77×** |
| **H** | KNN recommendations | `app/ml/recommendations/` | Only **2 of 7** features can support a population-grounded claim; the rest are restricted to personal-deviation claims |

### Why the headline is the classifier, not the PCA score

The PCA score was built first and looked like the obvious headline: a single
0–100 number. It was then validated against the dataset's own risk labels and
**failed** — it separated Low from the rest but barely distinguished Medium from
High (mean score 53.4 vs 54.4). The classifier, by contrast, reaches ROC-AUC
0.802 against a 0.383 majority baseline.

So the classifier leads the Home screen, and the PCA number appears on Insights
as a "behaviour index" carrying its failed-validation caveat. The score was
neither quietly deleted nor quietly promoted.

### Features excluded, and why

`gaming_hours`, `notifications_per_day`, `app_opens_per_day` and `age` were
dropped from the classifier because their **measured** mutual information with
the target was 0.0000–0.0035 — effectively none. That is a measurement, not a
preference.

---

## Data integrity rules

Each of these is enforced in code and covered by tests, and each was written
after a real defect:

**Demonstration data stays distinguishable from real data.** Rows carry a
`source` of `device`, `manual`, `csv` or `synthetic`. Every analysis endpoint
takes a `scope` parameter, and each model artifact records which sources it was
fitted on. A forecaster fitted on generated days shows a *"Demonstration, not a
forecast about you"* banner — without it, those predictions are
indistinguishable from personal ones.

**A real measurement always beats generated filler.** An early version of the
upsert rule protected synthetic rows from being overwritten. The device read 7
genuine days, 6 collided with demo days and were refused, and only 1 was
stored. Protecting invented data from real data is backwards.

**A day still in progress is not a day.** Today's totals are still
accumulating, so every model excludes the current date. This was measured: a
7-minute morning sitting at `lag_1` pulled the next-day forecast down to 5.0 h
while the preceding seven complete days averaged 8.7 h. The classifier's
averaged input was 7.62 h/day with today included and 8.69 h/day without. Home
and Usage still show today's figure, where it is labelled as the day so far.

**A day with no reported activity is unknown, not zero.** Android retains
detailed events for about a week. Storing an older empty day as zero would feed
the personal baseline a fabricated quiet day and make genuine quiet days look
normal by comparison.

**Nothing is imputed.** A missing measurement is reported, never filled in — an
imputed lag would be an invented observation.

**A missing measurement is never reported as a missing day.** They have
different remedies — one is fixed by time, the other is not — so they are
different exceptions with different messages. This was got wrong in four
separate modules before it was found and tested; see
[Honest refusals](#honest-refusals).

**No leakage.** Every forecasting feature is a lag or rolling statistic of
*prior* days. `TimeSeriesSplit` replaces `KFold`, because shuffling days would
train on the future to predict the past. Scaling happens inside the `Pipeline`,
so fold statistics never leak across folds. The population dataset's
`addicted_label` was dropped once it was found to be a perfect function of
`addiction_level`.

---

## Honest refusals

When a model cannot run, the API returns **409 with a reason**, never a
placeholder. Getting these messages right took three attempts:

> ❌ `"Not enough history to assign a persona - 7 day(s) recorded, 7 needed."`
>
> Seven recorded, seven needed, and still refused. The real blocker was
> notification counts, which Android cannot backfill. "Record more days" would
> not have fixed it.

> ❌ `"Not enough complete days to score - 0 usable, 7 needed."`
>
> Reads as "your days are no good". The real cause was a single measurement
> that began on Thursday.

> ✅ `"8 day(s) of history are available, but no single day carries every
> measurement this model needs. Missing: notifications per day (missing on 7 of
> 8 days); sleep hours (missing on 2 of 8 days). The model becomes available
> once enough days carry all of them…"`

---

## Current state on real data

With 8 measured days on the author's device:

| Module | Status |
|---|---|
| Risk classification | ✅ **High (56%)**, averaged over 7 complete days, 8.69 h/day |
| Forecast | ✅ runs, labelled a demonstration — see below |
| Persona | ⛔ refuses: needs notification counts, which began part-way through 2026-11-06 |
| Behaviour index (PCA) | ⛔ refuses: same cause |
| Recommendations | ⛔ refuses: same cause |
| Anomaly detection | ⛔ refuses: a personal baseline needs 14 days |

That the classifier keeps working while three other models refuse is the
practical payoff of excluding zero-information features: `notifications_per_day`
was dropped from the classifier at training time, so the Home screen is
unaffected by a field that only started being recorded today. The persona, the
behaviour index and the recommender were each fitted *with* that field and
therefore need it.

**The forecaster is not yet fitted on this user's data.** Eight measured days
collapse to a single usable training row once the 7-day lag warm-up is
consumed, and 14 rows are required — so the requirement is **21 consecutive
measured days**. The app states the shortfall rather than implying the forecast
is personal. Until then it serves a model fitted on 359 generated days plus 7
measured ones, behind the demonstration banner.

---

## Known limitations

- **Daily battery consumption is never available.** Android retains no
  historical record of it, so the field is left blank rather than estimated.
- **Android forgets usage after about a week**, so the app captures each day to
  local storage on the phone as soon as it reads it — on launch, on return to
  the foreground, and via a background task roughly twice a day. Uploading to
  the backend is a separate, retryable step, because the capture is the half
  with a deadline and the backend (a laptop over a hotspot) is usually
  unreachable. A re-read that reports *less* than an already-captured day is
  refused rather than stored, since a day whose events have aged out comes back
  looking like a quiet day.
- **Notification counts cannot be backfilled**, and a count is kept only for a
  day the listener observed *from start to finish*. The day access is granted is
  therefore reported as unknown rather than low, and so is any day inside a gap
  where access had been revoked — the alternative is a tally of a few hours that
  reads as a quiet day. Coverage is recorded as real observation windows
  (`onListenerConnected` / `onListenerDisconnected`), checked against each day's
  own length so a daylight-saving day is judged correctly, and re-checked in the
  backend rather than trusted from the client. Up to 15 unobserved minutes are
  tolerated, because Android rebinds listeners routinely and a zero tolerance
  would let a single reboot discard a whole day's count.
  `/api/data/sync-status` reports `field_first_seen`, so the app shows
  *"1 of 8 days · from 2026-11-06"* rather than an unexplained gap.
- **Sleep is an estimate**, inferred from the longest period of inactivity and
  accepted only within a 4–11 h plausibility band; a figure outside the band is
  discarded rather than stored. It is always labelled an estimate.
- **Screen time matches Digital Wellbeing to within a few minutes**, after two
  corrections: sessions are delimited by `ACTIVITY_RESUMED` only (per-activity
  `PAUSED`/`STOPPED` events were closing live sessions early), and launcher time
  counts toward screen time while launcher opens do not count as app launches.
- **Clustering found no real structure** (silhouette 0.107), so a persona is
  reported as the user's *closest* group rather than a distinct type.
- **The project must not live in a OneDrive-synced folder.** File watchers fire
  continuously there, which breaks Metro's Fast Refresh. A path containing a
  space also breaks the Android NDK toolchain.

---

## Tests

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/ -q      # 141 passed
```

```bash
cd android
./gradlew :usage-stats:testDebugUnitTest           # 15 Kotlin tests
```

The Kotlin tests cover the capture merge rules, whose failure mode is the
silent and permanent loss of measurements Android can no longer supply. Each
rule was verified load-bearing by deleting it and re-running — which caught one
rule that no test actually exercised, and a test that was passing for the wrong
reason.

Each regression test names the defect it pins and the number that defect
produced, so a future change that reintroduces it fails with an explanation
rather than a bare assertion. The tests guarding the two most recent fixes were
checked to go **red** when the fix is removed.

```bash
npx tsc --noEmit                                   # frontend typecheck
```

---

## Datasets

Two public Kaggle datasets back the population models. They are documented in
[backend/README.md](backend/README.md#datasets) with verified facts about each —
including that pandas reads their literal `"None"` strings as NaN, which
silently deleted 819 of the lowest-risk rows before it was caught.
