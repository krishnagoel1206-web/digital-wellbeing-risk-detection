# Backend - Digital Wellbeing Risk & Deviation Detection API

FastAPI + SQLite + scikit-learn backend for the mobile app.

## Requirements

- Python 3.15 (verified on CPython 3.15.1, win_amd64)

## Setup

From the `backend/` directory:

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.txt
```

`--only-binary=:all:` forces prebuilt wheels, so no C compiler is needed.

### Why not `uvicorn[standard]`

The `standard` extra depends on **PyYAML, which has no cp315 wheel at all**, so
it cannot install on Python 3.15 without a build toolchain. PyYAML is only used
by uvicorn to read YAML `--log-config` files, which this project does not use.
`requirements.txt` therefore pins plain `uvicorn` plus the two useful pieces of
the extra that *do* ship 3.15 wheels: `watchfiles` (for `--reload`) and
`httptools`.

## Running

```bash
.venv/Scripts/python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` is required for a phone on the same Wi-Fi/hotspot to reach the
API. Binding to `127.0.0.1` makes it reachable only from this laptop.

- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/health>

### Windows firewall

Windows blocks inbound connections to Python by default, so the phone's
requests to port 8000 will **time out with no error message** until an
inbound rule exists. In an **Administrator** PowerShell, once:

```powershell
New-NetFirewallRule -DisplayName "FastAPI Dev 8000" -Direction Inbound `
  -LocalPort 8000 -Protocol TCP -Action Allow -Profile Any
```

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

Each test gets its own throwaway SQLite file, so tests never touch the
development database.

## Datasets

Not committed - download these into `data/raw/`:

| File | Source | Role |
|---|---|---|
| `Smartphone_Usage_And_Addiction_Analysis_7500_Rows.csv` | [Kaggle](https://www.kaggle.com/datasets/zahranusratt/smartphone-usage-and-addiction-analysis-dataset) | Primary - feature-rich (7,500 rows) |
| `Students Social Media Addiction.csv` | [Kaggle](https://www.kaggle.com/datasets/zahranusratt/student-social-media-addiction-analysis-dataset) | Integrity check - real self-reported label (705 rows) |

### Verified facts about these datasets

Established by direct inspection, not assumption:

- The primary dataset's `addiction_level` has **four** values: `None` (819),
  `Mild` (1373), `Moderate` (2874), `Severe` (2434) - not Low/Medium/High.
  Mapped to the app's three classes as
  `None`+`Mild` -> Low, `Moderate` -> Medium, `Severe` -> High.
- `addicted_label` is a **perfect deterministic function** of `addiction_level`
  (0 for None/Mild, 1 for Moderate/Severe, zero exceptions). Using both would
  be data leakage, so only one is ever used as the target.
- `user_id` holds 7,500 distinct values across 7,500 rows: **one row per
  person, no longitudinal data.** The personal-baseline module therefore works
  from the user's own daily records in SQLite, not from this dataset.
- Screen time spans exactly 3.00-12.00 hours with class ranges that overlap, so
  the label is *not* a simple threshold on screen time. A rigorous circularity
  test is part of Phase 4.

## Architecture

```
main.py                     app factory, CORS, router mounting
app/config.py               all settings; nothing else hard-codes paths/ports
app/database/models.py      SQLAlchemy ORM models
app/database/session.py     engine, session, SQLite PRAGMAs
app/schemas/                Pydantic validation at the API boundary
app/services/               business logic, independently testable
app/api/                    thin HTTP handlers
app/ml/                     training and inference, strictly separated
app/ml/artifacts/           saved .joblib models + metrics JSON (build output)
```

### Design decisions

- **Durations are stored as integer minutes**, not float hours. The source
  datasets use fractional hours, converted on import. Integers avoid float
  drift when ratios and weekly sums are computed.
- **Only `record_date` and `total_screen_minutes` are required.** Every other
  behavioural column is nullable, because no field may be assumed to exist -
  manual entry cannot supply battery drain, and neither dataset supplies call
  duration.
- **Every record carries a `source`** (`manual` / `csv` / `synthetic`) so
  demonstration data is always distinguishable from real user data at query
  time rather than by convention.
- **A duplicate date returns HTTP 409 rather than overwriting.** Silently
  replacing a day's data would make the stored history untrustworthy.
- **Partial updates re-validate the merged record.** Sending only
  `social_minutes` is valid alone but may be impossible once combined with the
  stored total, so the whole row is re-checked before committing.
- **`/api/health` reports which models are actually trained**, letting the app
  hide ML sections instead of showing placeholder numbers.

## Running with auto-reload

Pass `--reload-dir app` as shown above. Without it, WatchFiles also watches
`.venv/` and will spuriously restart the server when it touches site-packages
files (observed with scikit-learn), which looks like a bug but is not.

## Training models

```bash
.venv/Scripts/python.exe scripts/seed_synthetic_history.py --days 365 --clear
.venv/Scripts/python.exe scripts/train_models.py
```

Training is the ONLY place models are fitted. The API loads the `.joblib`
artifacts and calls `predict()`; it never trains. If an artifact is absent,
`/api/health` reports the model as untrained and the endpoints return HTTP 409
with instructions, rather than a placeholder number.

Note: `--reload-dir app` does not watch `main.py`, so restart the server
manually after editing it.

## Status

**Phases 3-5 complete. 47 passing tests.**

### Phase 5 measured results

Classification on 7,500 rows, 5 features, majority baseline **0.3832**:

| model | CV acc | CV F1 | test acc | test F1 | ROC-AUC |
|---|---|---|---|---|---|
| Bagging *(selected)* | 0.6245 | 0.6379 | 0.5973 | 0.6141 | 0.8021 |
| RandomForest | 0.6233 | 0.6376 | 0.5900 | 0.6072 | 0.7969 |
| GradientBoosting | 0.6162 | 0.6301 | 0.6167 | 0.6328 | 0.8067 |
| DecisionTree | 0.6052 | 0.6193 | 0.5913 | 0.6104 | 0.8014 |
| SVM | 0.6055 | 0.6176 | 0.5780 | 0.5908 | 0.7921 |
| KNN | 0.6013 | 0.6124 | 0.6073 | 0.6198 | 0.8046 |
| LogisticRegression | 0.5825 | 0.5788 | 0.5647 | 0.5574 | 0.7752 |

Accuracy ~60% matches the ceiling measured in Phase 4 (~0.62). ROC-AUC ~0.80
is markedly better than accuracy alone suggests, and the confusion matrix
explains why: **Low is separated well, but Medium and High are heavily
confused** - consistent with the Phase 4 finding that their mean screen time
differs by only 8.35h vs 8.60h. Low and High are never confused with each
other (0 in both off-diagonal corners).

Regression on 358 usable days, naive-persistence baseline RMSE **75.95 min**:

| model | RMSE | MAE | R² | beats naive |
|---|---|---|---|---|
| Lasso *(selected)* | 57.88 | 45.08 | 0.4470 | yes |
| Ridge | 58.05 | 44.99 | 0.4437 | yes |
| LinearRegression | 58.25 | 45.25 | 0.4399 | yes |

All three beat the naive baseline by ~24%. The three are nearly tied, so the
multicollinearity advantage is real but modest here. Lasso's L1 penalty drove
three coefficients to exactly **0.000** (`lag_2`, `lag_7`, `roll_mean_3`),
which is the feature-selection behaviour that distinguishes it from Ridge.

Re-running `train_models.py` reproduces these numbers exactly.

### Two defects found and fixed during Phase 5

1. **ROC-AUC was `n/a` for all seven models.** `roc_auc_score` requires
   `labels` in sorted order, but severity order (`Low, Medium, High`) was
   passed - and the `except` block silently turned the error into `None`.
   The probability columns are now reordered to sorted labels, and any genuine
   failure is recorded in `roc_auc_error` instead of vanishing.

2. **All three regressors initially failed** (negative R², none beating naive).
   Cause: the synthetic generator drew each day independently, so the series
   had no autocorrelation and lag features were uninformative by construction.
   The generator now uses an AR(1) process (rho = 0.55) plus a slow trend.
   Verified afterwards: lag-1 autocorrelation 0.522, lag-7 0.434.

### Phase 6 measured results

Train with:

```bash
.venv/Scripts/python.exe scripts/train_clusters_and_score_anomalies.py
```

#### Anomaly detection: 100% recall against ground truth

Scored against the three anomalies the generator deliberately injected:

| injected day | kind | result | evidence |
|---|---|---|---|
| 2026-05-22 | late-night spike | **detected** (severe) | `late_night_ratio` z = **+8.61**, `sleep` z = −5.23 |
| 2026-09-02 | notification flood | **detected** (severe) | `notification_count` z = **+19.25** |
| 2026-09-23 | social binge | **detected** (mild) | `total_screen_minutes` z = **+4.00** |

**Recall 100% (3/3). Precision 30%** (10 of 365 days flagged). Low precision
is expected rather than a defect: the history is randomly generated, so some
non-injected days genuinely are extreme, and flagging them is correct.

The late-night case is the one that matters. Total screen time that day was
450 min against a baseline of 411 - entirely unremarkable, z ≈ 0.5. **A fixed
screen-time threshold would have missed it completely.** The detector found it
through the late-night share and reduced sleep, and correctly did *not* cite
screen time as a reason. There is a dedicated test for this
(`test_late_night_shift_is_caught_with_screen_time_unchanged`).

Three further days were flagged on the **Mahalanobis combination** alone - no
single feature was deviant, but the combination was rare for this user.

Method notes:
- **Median + scaled MAD**, not mean + standard deviation. One 14-hour binge
  inflates the standard deviation enough to make itself look ordinary; the
  outlier masks itself. Tested in `test_a_single_extreme_day_does_not_mask_itself`.
- **Leave-one-out**: a day never contributes to the baseline it is judged
  against.
- **Nothing is compared to the population.** A habitual 8-hour user has an
  8-hour baseline. Tested in
  `test_heavy_user_is_not_flagged_for_a_normal_heavy_day`.

#### Clustering: no meaningful structure found

This is a negative result, reported as such.

| algorithm | clusters | noise | silhouette | Calinski-H | Davies-B |
|---|---|---|---|---|---|
| KMeans | 2 | 0.0% | **0.1070** | 908.0 | 2.823 |
| DBSCAN | 1 | 0.1% | n/a | n/a | n/a |
| BIRCH | 2 | 0.0% | 0.0628 | 465.2 | 3.443 |

- Silhouette **0.107** = *weak*; conventionally >0.5 indicates real separation
- Silhouette varies by only **0.011** across k = 2..6, so the "best" k is
  arbitrary - the report flags `selection_is_meaningful: false`
- KMeans vs BIRCH agreement (adjusted Rand index) **0.026** - the two
  algorithms essentially disagree
- DBSCAN puts everything in one cluster

**Why:** Phase 4 measured all feature pairs at r ≈ 0.00. Independently sampled
features fill the space uniformly, so there are no natural groups to find.
K-Means always returns k clusters whether or not any exist, so every response
carries the structure verdict and the `/api/ml/persona` endpoint reports
`assignment_was_close_run` when the two nearest centroids are within 25%.
Clustering the user's own 365 days scored 0.109-0.128 - no better.

Personas are still derived programmatically from centroid values
("Heavy overall use" / "Light overall use"), never hand-assigned. And a
**"late-night scroller" persona cannot be produced from this dataset at all**,
because it contains no late-night column; naming one would mean labelling a
cluster with a behaviour never measured.

#### One parameter bug fixed

DBSCAN initially reported **0 clusters and 100% noise** with a hard-coded
`eps=0.9`. In a 7-dimensional scaled space that distance is too small for any
point to have neighbours. `eps` is now derived from the data (90th percentile
of the distance to the 14th nearest neighbour → 1.430), with the k-NN distance
distribution reported for audit.

### Phase 7 measured results

```bash
.venv/Scripts/python.exe scripts/train_risk_score.py
```

#### PCA found no structure, and the score did NOT validate

Explained variance per component, on 7 features:

```
PC1 0.1509   PC2 0.1461   PC3 0.1440   PC4 0.1429
PC5 0.1408   PC6 0.1390   PC7 0.1363
```

Every component explains about **1/7 = 0.1429** of the variance. That is the
signature of a covariance matrix close to the identity: PCA is performing an
arbitrary rotation because there is no correlated structure to find. It needs
**6 of 7 components** to reach 80% cumulative variance, i.e. essentially no
dimensionality reduction. This is the same root cause as the Phase 6
clustering failure - Phase 4 measured all feature pairs at r ≈ 0.00.

Validation against the dataset's own labels:

| metric | value |
|---|---|
| Spearman rho vs risk ordinal | **+0.1816** (p = 1.2e-56) |
| mean score, Low | 40.68 |
| mean score, Medium | 53.41 |
| mean score, High | **54.39** |
| monotonic Low < Medium < High | true |
| **verdict** | **NOT VALIDATED** |

Monotonic, but Medium and High are **under one point apart**. The score
separates Low from the rest and then goes flat - precisely mirroring the
classifier's confusion matrix, from the same cause.

Kernel PCA (RBF) scored better (rho **+0.3098**) but is still weak, and kernel
components carry no feature loadings, so a score built on them cannot explain
*which* behaviour drove it. Linear PCA was kept for explainability, with the
failure reported rather than hidden.

Consequence for the app: `/api/ml/risk-score` always returns
`suitable_as_headline_risk: false` plus a caveat. **The classifier
(ROC-AUC 0.80) is the better-supported risk signal.**

#### Bayesian confidence layer works correctly

Beta-Binomial posterior over the user's underlying elevated-risk-day rate.
Prior: **Jeffreys Beta(0.5, 0.5)** - the non-informative reference prior for a
proportion, preferred over uniform Beta(1,1) because elevated days are rare
and a uniform prior would drag small-sample estimates toward 50%.

| days | n_eff | rate | 95% credible interval | width |
|---|---|---|---|---|
| 5 | 1.6 | 31.6% | [0.6%, 85.7%] | 85.1% *(too weak)* |
| 10 | 3.2 | 27.2% | [1.3%, 72.3%] | 71.0% *(too weak)* |
| 30 | 9.5 | 22.9% | [4.2%, 51.2%] | 46.9% *(too weak)* |
| 90 | 28.4 | 21.0% | [8.6%, 37.2%] | 28.6% |
| 365 | 115.3 | 20.3% | [13.5%, 28.0%] | 14.5% |

**The violated assumption, and the correction.** The Binomial likelihood
assumes independent days. Phase 5 measured lag-1 autocorrelation of screen
time at **0.522** - consecutive days are correlated, so they carry less
information than independent trials and the naive interval is too narrow.
Corrected with the AR(1) effective sample size
`n_eff = n (1 - rho) / (1 + rho)`:

```
n = 365, k = 73
assuming independence : [16.1%, 24.3%]  width  8.2%  (n_eff 365)
corrected for rho=0.52: [13.5%, 28.0%]  width 14.5%  (n_eff 115)
-> 1.77x wider; ignoring autocorrelation would overstate certainty
```

The correction is data-driven, not hard-coded: applied to the real stored
history, the *score* series has rho = 0.0897 (lower than screen time, because
the composite mixes in uncorrelated features), giving n_eff = 304.9 of 365.

Both behaviours are asserted in tests: the interval must narrow with more
data, and the autocorrelation correction must widen it.

### Resolved: auto-reload was unreliable on OneDrive

`uvicorn --reload` repeatedly failed to pick up changes while the project lived
in a OneDrive-backed folder, leaving a stale process serving old routes. The
project was moved to `C:\dev\my_mlproj` for this reason (and because a space in
the old path broke the Android NDK toolchain). The habit below is still worth
keeping, since a silently stale backend is an expensive thing to debug — run
**without** `--reload` and restart deliberately:

```bash
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Phase 8 measured results

```bash
.venv/Scripts/python.exe scripts/train_recommender.py
```

#### Only two features can support a risk-related recommendation

Mutual information with the risk label, measured at training time (not
hard-coded), decides whether a feature may ground a population claim:

| feature | mutual info | population-grounded? |
|---|---|---|
| daily_screen_time_hours | 0.25283 | **yes** |
| social_media_hours | 0.17309 | **yes** |
| work_study_hours | 0.01106 | no (and not advisable anyway) |
| app_opens_per_day | 0.00572 | no |
| notifications_per_day | 0.00456 | no |
| gaming_hours | 0.00118 | no |
| sleep_hours | 0.00000 | no |

Consequence: the brief's example suggestion *"manage notification frequency"*
**cannot be justified as a risk matter from this dataset** - notification
volume and the risk label are measurably unrelated here. Such features can
still raise a personal-baseline suggestion ("this is unusual for you"), which
is a weaker and differently-labelled claim.

#### Claim discipline

The dataset is cross-sectional: 7,500 people observed once each, nobody
followed over time. So:

| | |
|---|---|
| **Allowed** | "People whose usage resembles yours and who are classified lower-risk typically spend 1.2 fewer hours on social media." |
| **Forbidden** | "Reducing social media will lower your risk." |

Every recommendation carries `evidence_basis`
(`population_comparison` / `personal_baseline`) and `claim_type`
(`associational` / `personal_deviation`). There is a test asserting no
recommendation ever contains an outcome promise.

The comparison group is the **low-risk subset of the user's nearest
neighbours** (k = 75), because comparing against all neighbours would only
describe people like the user. If fewer than 10 of them are Low risk, the
comparison is refused with a stated reason rather than computed on a handful
of records.

#### A threshold bug found and fixed

The engine initially produced **zero** recommendations. Cause: a purely
*relative* gap threshold (15%) penalises heavy users. A user averaging
6.67 h/day against a low-risk peer median of 5.70 h/day has a gap of
**0.97 h - nearly an hour of every day** - but only 14.6% of their own total,
so it was silently discarded. Absolute per-feature minimums now apply
alongside, and either threshold can qualify a gap. Regression-tested.

Phase 9 next: wiring the mobile screens to these APIs.

### Key Phase 4 findings (measured, not assumed)

| Finding | Consequence |
|---|---|
| `addiction_level` contains the literal string `"None"` for 819 rows (10.92%), which pandas reads as NaN by default | A naive `dropna()` deletes the entire lowest-risk class. Loaders disable the default NA strings. |
| `addicted_label` is a perfect function of `addiction_level` | Dropped on load - keeping it would leak the target. |
| Achievable accuracy on `risk_class` ≈ **0.62** (majority baseline 0.38) | The label is genuinely learnable but not deterministic. Expect ~60%, not 99%. |
| `gaming_hours`, `notifications_per_day`, `age` have mutual information **0.0000** with the label | Excluded from the classifier; retained for clustering and anomaly detection. |
| `weekend_screen_time` correlates **0.964** with `daily_screen_time_hours` | Severe multicollinearity - the data-driven reason Ridge/Lasso should beat plain Linear Regression. |
| 60.7% of rows have category hours exceeding total screen time | Ratio features must be clipped; clipping counts are reported, not hidden. |
| Both population datasets are cross-sectional (7,500 distinct ids / 7,500 rows) | No longitudinal data, so the personal baseline uses the user's own records plus the synthetic generator. |
| Zero IQR outliers in every numeric column; tidy bounds (screen time exactly 3.00-12.00) | Strong evidence the data is synthetic. Must not be presented as real-world validation. |

---

## Phase 10 measured results: testing and data integrity

Phase 10 was a verification pass over the whole pipeline against the author's
real device data. It surfaced **seven defects**, each of which produced a wrong number or a
misleading message visible in the app. All seven are now fixed and tested, and
every rule added here was checked to go red when the fix is removed.

Three of the seven were found only by following up on something else: defect 5
by fixing defect 2, defect 6 by verifying a claim about it that turned out to
be wrong, and defect 7 by reading the access log after a restart that was
supposed to be routine. None would have been found by reasoning about the code
alone.

### 1. A discredited sleep estimate could not be withdrawn

`2026-11-05` showed `sleep_hours = 13.6`. That figure was collected while the
plausibility band was wider; the band was later tightened to 4-11 h on the
device, so the phone correctly stopped sending it. The value nevertheless
survived, because sync Rule 2 at the time said *a null never overwrites a
stored value*.

Two separate problems, fixed separately.

**The rule was too broad.** Fields fall into two groups, and a blanket rule
gets one of them wrong:

| | Meaning of a null | Behaviour |
|---|---|---|
| `notification_count`, `battery_drain_percent` | "I cannot see this" - Android keeps no history | never overwrites |
| everything else | "I looked, there is nothing to report" | overwrites |

**The band lived only in the Kotlin.** The backend accepted any sleep figure
from 0 to 24, so a client could store an implausible one and it would then
outlive the rule that rejected it. The band is now enforced in
`app/schemas/sync.py` as well, where data persists:

- An **estimated** figure outside 4-11 h is **discarded**, not rejected with a
  422. Refusing the whole day would throw away genuine screen time because one
  derived field came out wrong.
- A **measured** figure is left alone. The band describes what can be inferred
  from a gap in phone activity; it has no authority over an observation.
- The discard is reported in the sync response rather than leaving a silent
  blank.

Verified on the real row: `13.6` became `null`, with `total_screen_minutes` and
every other measured field unchanged.

### 2. A day still in progress was treated as a complete day

The largest-impact defect found. Today's totals are still accumulating, so the
row is a day-**so-far**. Every model treats a row as a day, and each was wrong
in its own way:

| Model | Effect, measured on real data |
|---|---|
| Forecaster | A 7-minute morning at `lag_1` - the most influential feature - pulled the next-day forecast to **5.0 h** while the preceding seven complete days averaged **8.7 h** |
| Classifier | Averaged input **7.62 h/day** with today included, **8.69 h/day** without |
| Persona | Same dilution, shifting the assignment toward a lighter user |
| Anomaly detection | Would flag the day in progress as unusually quiet, every morning |

Fixed with one rule in one place: `drop_incomplete_today()` in
`app/ml/regression/train_forecast.py`, applied by `_daily_frame()` in
`app/api/ml.py` so every model inherits it, and by `train()` so no partial day
becomes a training row.

The rule is **unconditional** - the current date is excluded regardless of how
large its total already is. Any size threshold would need to know what a normal
day looks like for this person, which is the quantity being estimated, and at
23:50 a complete-looking day is still not complete.

Two deliberate consequences:

- With today removed, the **first forecast day is today**, predicting the whole
  day it will become. It is flagged `is_current_day` and will not match the
  smaller figure on the Usage screen; the app says so rather than presenting
  two contradictory numbers.
- Home and Usage still display today's figure, where it is correctly labelled
  as the day so far. The rule guards the models only.

### 3. The forecaster was fitted on invented days and did not say so

The artifact recorded `n_samples: 358` and nothing about provenance, so a model
fitted on 365 generated days could be served under `scope=device` and look
exactly like a personal forecast.

Training now records its composition, counted from the rows themselves rather
than from what the caller intended:

```json
"training_data": {
  "rows_by_source": { "synthetic": 359, "device": 7 },
  "contains_synthetic": true,
  "fitted_on": "mixed",
  "note": "Fitted on 7 measured day(s) and 359 generated demonstration day(s)..."
}
```

`--sources device|real|all` selects what to train on. The API turns the record
into a caveat, and the app into a **"Demonstration, not a forecast about you"**
banner - a banner rather than small print, because what it corrects is a wrong
reading of the numbers directly above it.

**An honest negative result:** a device-only fit is not yet possible.

```
$ scripts/train_models.py --only regression --sources device
scope              : device
loaded 8 daily record(s) from the database
by source          : device=8

Not enough history: need at least 14 usable days to train, have 1
```

Eight measured days collapse to one usable row once the 7-day lag warm-up is
consumed. The requirement is `MIN_ROWS_TO_TRAIN + LAG_WARMUP_DAYS` = **21
consecutive measured days**, which the API now reports as a `personal_model`
block so the app can state the shortfall in days the user can count.

Training refused *before* saving, so the existing artifact was left intact - a
failed training run does not leave the app with no model.

### 4. A refusal blamed the wrong thing, for the second time

The first version of this message conflated "too few days" with "a measurement
is missing". That was fixed by checking whether a column was null on **every**
day - which stopped firing the moment notification access was granted and one
day carried a count:

```
8 device days stored, 7 needed, and still:
  "Not enough complete days to score - 0 usable, 7 needed."
```

Diagnosis of the real cause:

| PCA feature | Missing on |
|---|---|
| `notifications_per_day` | 7 of 8 days |
| `sleep_hours` | 2 of 8 days |
| the other five | 0 days |

The check now asks the useful question - which measurements are discarding
rows, and on how many days - rather than the narrow one of whether a column is
empty throughout:

> 8 day(s) of history are available, but no single day carries every
> measurement this model needs. Missing: notifications per day (missing on 7 of
> 8 days); sleep hours (missing on 2 of 8 days). The model becomes available
> once enough days carry all of them - which depends on the measurement:
> notification counts accrue from the day access was granted, while a sleep
> estimate appears only on days with a clear period of inactivity to infer it
> from.

The wording deliberately stops short of promising every field will appear.
Notification counts will, now that access is granted; a sleep estimate only
appears on days with a clear enough period of inactivity.

`/api/data/sync-status` also now reports `field_first_seen`, so the Data screen
shows *"notification count: 1 of 8 days - from 2026-11-06"* instead of a bare
count that reads as a fault.

### A functional regression, accepted deliberately

Excluding the day in progress made the **persona refuse** where it previously
returned "Heavy overall use". That answer had been computed with
`notifications_per_day = 11.0` drawn from a single partial day, while the
response advertised `days_averaged: 8`.

A refusal that names its cause is better than an answer resting on one partial
day presented as a week's average. It resolves on its own as notification days
accrue.

### 5. "A missing measurement is not a missing day", got wrong four times

Fixing the incomplete-day rule exposed a fifth defect, and it turned out to be
the same mistake repeated in four modules. Code that had detected an absent
FEATURE raised `InsufficientHistory`, whose message talks about DAY COUNTS:

```
Not enough history for recommendations - 7 day(s) recorded, 7 needed.
```

Seven recorded, seven needed, and still refused. A user reading that collects
more days, which cannot possibly help, because the blocker was a measurement
that was never there. The two conditions have different remedies:

| Condition | Remedy | Exception |
|---|---|---|
| too few days | time fixes it | `InsufficientHistory` |
| a measurement is absent | time may never fix it | `MissingFeatures` |

The conflated raise sites were:

| Module | Line | Detected |
|---|---|---|
| `classification/inference.py` | 234 | non-finite averaged feature |
| `recommendations/inference.py` | 229 | non-finite user profile entry |
| `anomaly_detection/personal_baseline.py` | 216 | no feature with enough non-null days |
| `risk_scoring/inference.py` | (earlier fix) | column null on every day |

All four now raise `MissingFeatures`, and the API maps it to 409 at every call
site rather than letting it escape as a 500.
`tests/test_refusal_messages.py` pins each one.

**A feature-set discovery while writing those tests.** The first version
blanked `notification_count` to make the classifier refuse, and it passed
vacuously - the classifier *never used* notifications, because its measured
mutual information with the target was 0.0000 and it was excluded at training
time. The test now blanks `sleep_hours`, which the classifier does use, and
asserts the exclusion explicitly.

That exclusion turns out to carry a practical benefit nobody designed for:
with notification access granted only today, the **Home screen headline still
works** while the persona, the behaviour index and the recommender all refuse.
Dropping a zero-information feature made the most important model the most
robust one.

### End-to-end sweep

Every screen's endpoint, on measured data only (`scope=device`):

```
9 served, 4 refused-with-reason, 0 broken
```

Four refusals rather than the two seen mid-phase. Both of the new ones are
consequences of the incomplete-day rule: the recommender and the persona had
been running on `notifications_per_day = 11.0`, a figure drawn from a single
partial day. Each now refuses and names the cause.

### Test suite

```
141 passed
```

| File | Tests | Covers |
|---|---|---|
| `test_sync.py` | 29 | upsert rules, the sleep plausibility band, notification coverage |
| `test_forecast_integrity.py` | 13 | the incomplete-day rule at model and API level, provenance recording, refusing to train on too little history |
| `test_refusal_messages.py` | 12 | the day-versus-measurement distinction at every raise site |
| `test_usage_api.py` | +4 | provenance filtering on the records endpoint |

Both recent fixes were verified load-bearing by removing them and confirming
the guarding test fails:

```
assert dropped[0].date == dt.date.today().isoformat()
E  AssertionError: assert '2026-11-07' == '2026-11-06'

assert body["days_averaged"] == 8
E  AssertionError: today was counted as a day
E  assert 9 == 8
```

### 6. A notification count could describe part of a day, or none of it

Found while checking a claim I had made and got wrong. I told the user that
2026-11-06's notification count was a partial figure that would harden into a
fake full-day value. Reading the device settled it the other way:

```
$ adb shell run-as com.anonymous.my_mlproj cat .../usage_stats_notifications.xml
<int  name="count_2026-11-05"   value="72" />
<int  name="count_2026-11-06"   value="65" />
<long name="observing_since_ms" value="1793898532088" />   # 2026-11-05 22:38:52 IST
```

Observation began at 22:38 on **11-05**, so 11-06 was watched from midnight and
its count is genuine. The partially observed day is 11-05 - whose 72 covers 81
minutes - and the module was already nulling it correctly. **The defect I
described did not exist.**

A real one did, two lines away. `observing_since_ms` was written
`if (!store.contains(...))` - once, and never reset - and revoking notification
access does not clear SharedPreferences. So after revoke -> gap -> re-grant:

| | |
|---|---|
| `isEnabled` | true again |
| `observingSince` | still the ORIGINAL grant, before the gap |
| `observingSince <= dayStart` | passes for every day in the gap |
| result | `notificationCounts[key] ?: 0` -> a hard **0** |

Days nobody watched were recorded as a confirmed zero - "no notifications
arrived" - which is the exact fabrication the module's own comment forbids.

**Fix: real observation windows instead of one timestamp.**
`NotificationCounterService` now implements `onListenerConnected()` and
`onListenerDisconnected()` and stores windows as `start-end;start-end;start-`.
A day's count survives only if the windows cover that local day, within a
stated tolerance:

```kotlin
val unobserved = observable - observedMsOnDate(context, date, zone)
return unobserved <= MAX_UNOBSERVED_MINUTES * 60_000L      // 15 minutes
```

**The tolerance is not zero, and that was a correction mid-implementation.**
The first version demanded perfect coverage, which sounds stricter and is
simply wrong: Android unbinds notification listeners routinely - at reboot, on
app update, under battery-saver teardown - and rebinds within seconds. With a
zero tolerance one reboot would disqualify a whole day, so a phone restarted
daily would never report a single count. The feature would be technically
correct and useless. 15 minutes bounds the understatement at about 1% of a day
while still rejecting every case the rule exists for (a grant day leaves ~1359
minutes unobserved; a revoked day leaves all 1440).

Two further details the arithmetic has to get right:

- The day's length is **computed, not assumed to be 1440**, so a 23- or
  25-hour daylight-saving day is judged against its own length.
- For a day still in progress, coverage is measured against the **elapsed**
  part of it. Comparing against a full day would make every sync before
  midnight look like a coverage failure.

`onListenerDisconnected` is the callback that makes re-granting safe: the
system delivers it when the user revokes access, which is the case that
produced the fabricated zeros.

**Enforced in the backend too**, where data persists. Each day now carries
`notification_observed_minutes` and `notification_day_minutes`, and
`app/schemas/sync.py` discards a count whose coverage is short - reporting why,
and **keeping every other field**. Screen time, launches, unlocks, calls and
sleep come from `UsageStatsManager` and the call log, which are queried
historically and do not care whether a listener happened to be bound.

| Case | Count | Other fields |
|---|---|---|
| fully observed, 1440/1440 | kept | kept |
| granted mid-day, 81/1440 | **discarded** | kept |
| access revoked, 0/1440 | **discarded** | kept |
| DST short day, 1380/1380 | kept | kept |
| older app, no coverage sent | kept (unverifiable) | kept |

That last row is deliberate: treating "cannot verify" as "incomplete" would
wipe real notification history the moment the backend was upgraded ahead of the
app.

**Rule 2b, a necessary exception.** Rule 2 protects `notification_count` from
being wiped by a null, because Android cannot backfill it. But a rejection is
also a null, and refusing it would pin a discredited count in place forever -
the same trap as the 13.6 h sleep estimate. So the question became *whether the
device reached a conclusion*, not which field the null was for. See
`_rejected_fields()`.

**Two stale claims fixed while in there.** `countsByDate`'s docstring said it
omitted days before observation began; it never filtered anything. And
`observingSince` was recorded on the first counted *notification* rather than
at connect time, so a quiet morning after granting access was disowned - it is
now derived from the windows, and documented as display-only.

**Verified on the real device**, not just in tests. The build was installed and
notification access revoked and re-granted over adb:

```
before        observing_since_ms = 1793898532088        (legacy, no sessions yet)
after revoke  observation_sessions = 1793898532088-1793907792117
after regrant observation_sessions = 1793898532088-1793907792117;1793907792394-
```

The legacy timestamp migrated into a window and closed on
`onListenerDisconnected`; a fresh window opened on `onListenerConnected`. The
resulting coverage, computed from those windows:

| Date | Observed | Verdict |
|---|---|---|
| 2026-11-05 | 81 / 1440 min | count **dropped** - the grant day |
| 2026-11-06 | 74 / 75 min | count **kept** - the 277 ms test gap is immaterial |

A first dump taken immediately after re-granting showed no new window, which
looked like a failure of `onListenerConnected`. It was a race against
SharedPreferences' asynchronous `apply()`; the window was there on the next
read, and `dumpsys notification` confirmed the listener bound.

Also verified: `:usage-stats:compileDebugKotlin` BUILD SUCCESSFUL, 11 new
backend tests, and both new rules checked to fail when removed - the coverage
rule breaks 4 tests, Rule 2b breaks 1.

### 7. The Home dashboard was analysing demonstration data

Found by reading the backend's own access log after the user restarted the
app. Every request from the phone carried `scope=all`:

```
GET /api/ml/risk-classification?scope=all      200 OK
GET /api/ml/anomalies?limit=5&only_anomalous=true  200 OK   <- no scope at all
GET /api/ml/risk-score?scope=all               200 OK
```

`src/app/(tabs)/index.tsx` did not import `useScope` at all. Its four calls
therefore took the default, and the default was `'all'`:

```ts
export const getRiskClassification = (scope: AnalysisScope = 'all') => ...
```

So the **headline risk on the Home screen** - the single most prominent number
in the app - was computed over 359 generated demonstration days plus the
user's 8 measured ones, for as long as the screen has existed. Insights had
been scope-aware throughout, which is exactly why the two screens disagreed.
`analytics.tsx` had the same problem for its charts.

**The structural fault was the default.** `scope: AnalysisScope = 'all'` means
that forgetting the argument silently opts into demonstration data, and
nothing anywhere reports it. Removing the defaults turns the mistake into a
compiler error, and tsc then named every offender:

```
index.tsx(85,45):   error TS2554: Expected 1 arguments, but got 0.
index.tsx(90,40):   error TS2345: ...not assignable to parameter of type 'AnalysisScope'
index.tsx(92,46):   error TS2554: Expected 1 arguments, but got 0.
analytics.tsx(45):  error TS2353: ...
insights.tsx(116):  error TS2345: ...
```

Three further defects surfaced while fixing it:

- **`/api/usage/records` had no provenance filter**, so the trend chart could
  not exclude generated days even in principle. It now takes `sources`, and
  `getUsageRecords` requires the argument - `null` is the explicit way to ask
  for everything, which is correct for the Data screen (an inventory of what
  is stored) and wrong for any chart of behaviour.
- **The API client could not send a list parameter.** `String(value)` on an
  array produced `sources=device,manual`, which FastAPI rejects. Arrays are
  now sent as repeated parameters.
- **`DataSourceName` was missing `'device'`.** The TypeScript type had not
  been updated when the backend enum gained it, so the provenance that
  matters most was not expressible, and `records_by_source.device` could not
  be written at all.

Verified against the live database:

| Query | Rows |
|---|---|
| no filter (Data screen) | 367 - `device: 8, synthetic: 359` |
| `sources=device` (charts) | 8 - `device: 8` |

### Still outstanding

| Waiting on | Becomes available |
|---|---|
| Forecaster fitted on measured days | 21 consecutive measured days (8 so far) |
| Persona, behaviour index, recommender | 7 complete days carrying notification counts, i.e. from 2026-11-14 at the earliest |
| Anomaly detection | 14 days for a personal baseline |

None of these are code defects. Each is the system declining to produce a
number it cannot yet support, and each now says so in terms the user can act
on - or wait out.

---

## Phase 11: durable capture

The brief defined ten phases and Phase 10 completed them. This phase was not
in the brief; it addresses a gap that Phase 10's verification work exposed and
that would otherwise have quietly capped the project.

### The problem: the data is perishable

`UsageStatsManager` keeps its detailed event stream for roughly **one week**.
Everything derived from it - screen time, app categories, launches, late-night
minutes, unlocks, the sleep estimate - was reconstructed live on each request
and never stored on the device. Notification counts were the sole exception,
kept locally for 120 days.

So a day nobody read inside that week was **gone permanently**. No backend, no
retry and no later sync could recover it.

Capture also happened in exactly one place: the user pressing **Sync** on the
Usage Data screen. That made an unrecoverable outcome depend on a habit. And
because the personal forecaster needs **21 consecutive** measured days and the
personal baseline needs 14, a fortnight of not pressing the button does not
postpone those targets - it **resets the run**, since the gap can never be
filled.

### Capture and upload have different deadlines

The old `syncFromDevice` read Android and POSTed in one action, so an
unreachable backend threw the read away. That is the wrong shape for this data:

| | Expires? | Consequence of failure |
|---|---|---|
| **Capture** | yes, ~1 week | the day is lost for good |
| **Upload** | no | retry later; the upsert is idempotent |

And the backend is unreachable most of the time - it runs on a laptop reached
over a phone hotspot. So the two are now separate steps: capture to local
storage first, always; flush pending days if the backend answers. A failed
upload is a deferral, and the UI says *"Saved on your phone, not yet sent"*
rather than *"could not sync"*.

### Why a re-capture must not simply overwrite

The obvious implementation - store whatever the latest read says - destroys
the history it is meant to protect. Reading a date whose events have aged out
returns *no activity*, which is indistinguishable from a genuinely unused day.
Overwriting a captured Tuesday of 8.7 hours with the empty read taken nine
days later would erase a measurement and replace it with a fabricated quiet
day.

`CapturedDayStore.putDay` therefore refuses a worse read. The merge rules are
the real content of the class:

1. A read reporting **no activity never replaces** a stored day that had some.
2. For a **past** date, a read reporting **less screen time** never replaces
   the stored day. A completed day's total cannot shrink; if it appears to,
   events are aging out and the earlier, fuller read is the better measurement.
3. **Today is always replaced** - its total is still accumulating, so the
   newest read is by definition the most complete. Rule 2 deliberately does
   not apply, or the current day would freeze at its first reading of the
   morning.

A day Android reports no activity for is not stored at all, rather than stored
as a zero.

### Three triggers, because none alone is sufficient

| Trigger | Reliability |
|---|---|
| App launch / return to foreground | the one moment Android is certainly willing to run our code |
| Background task, every ~12 h | best-effort: WorkManager batches, defers in Doze, and stops after a force-stop |
| Sync button | unchanged, still useful for a visible manual run |

The background interval is 12 hours rather than 24 because Android treats it
as a floor and routinely stretches it, and a one-week retention window leaves
little room to overshoot. Foreground capture is throttled to once per 15
minutes so that flicking between apps does not re-read constantly.

Each pass re-reads a **full seven days** rather than just yesterday. That
redundancy is what makes the weak guarantees acceptable: the system only has
to succeed once a week for the history to stay unbroken.

### A bug found by running it, not by reading it

First run on the device, with the new store:

```
2026-10-31 … uploaded=false      <- would be re-sent forever
2026-11-05 … uploaded=true
```

Five of seven days stuck pending with nothing wrong. The client was inferring
which days the backend took by subtracting `skipped_detail` from what it sent -
and `skipped` conflates two opposite outcomes:

- *"already up to date; no values changed"* - **fully accepted**, nothing to do
- *"an existing record came from 'manual'"* - **genuinely refused**

The phone keeps captured days until the backend confirms them, so the first
kind stayed queued permanently. `SyncResponse` now carries an explicit
`accepted_dates`, and the client marks exactly those. Matching on the reason
text would have made a wording change silently break the queue.

After the fix, on the real device:

```
[capture:launch] read=7 new=0 updated=1 refused=0 sent=6 pending=0
```

```
7 day(s) tracked, 0 still pending
```

### Verified on the device

```
I ReactNativeJS: [background-capture] registered=true availability=2
D BackgroundTaskModule: registerTaskAsync: usage-stats-daily-capture
                        with options {minimumInterval=720.0}
```

`usage_stats_captured.xml` appeared on first launch holding seven days with
their notification coverage, unlock counts and sleep estimates. The oldest day
in the backend (2026-10-30) now sits **outside** the capture window - a
concrete illustration of the point: it survives only because it was read while
Android still had it.

### Honest limits

- **The degraded-read refusal is not yet exercised on real data.** Every stored
  day is still inside Android's window, so nothing has needed protecting. The
  path triggers naturally as dates age out, and `refusedDegraded` is reported
  on the Usage Data screen so it is observable rather than assumed.
  *Phase 12 covers the decision itself with unit tests, including the
  aging-out sequence, so the rule no longer rests on trust alone.*
- **The background task cannot be guaranteed.** Android may refuse it
  outright; the screen states that plainly rather than implying a schedule, and
  shows the **last successful capture** - without which a run of silent
  failures would be invisible until the missing days were already
  unrecoverable.
- **Upload marks a date done; the local day is kept, not deleted.** It cost a
  visit inside the retention window to obtain and can never be re-read, so the
  local copy remains the durable record and the history could be replayed if
  the backend database were ever lost.
- A day that expired before this phase existed is simply gone. 2026-10-30 is
  the earliest day this project will ever have.

---

## Phase 12: testing the capture merge rules

Phase 11 closed with an admitted gap: the merge rule that protects captured
history had **never been exercised**. Every stored day was still inside
Android's retention window, so nothing had yet needed protecting, and the one
code path whose failure means permanent data loss was running on trust.

This phase retires that caveat.

### Why this logic in particular

Every other defect in this project is recoverable. A model can be retrained, a
caveat reworded, a database rebuilt from the device. The merge rule is
different: Android discards its usage events after roughly a week, so a
measurement it loses cannot be obtained again from any source. The failure mode
is not an error - it is a real 8.7-hour day silently becoming a quiet one,
months before anyone looks at the chart and wonders.

### Making it testable

The decision was tangled up with `SharedPreferences`, a `Context` and JSON
parsing, so testing it would have meant standing up an Android runtime to
assert something about three booleans and two integers.

It now lives in `CaptureMerge.kt` as a pure function over a deliberately
narrow `DayFacts` - no Android imports, no I/O:

```kotlin
fun decideMerge(
  stored: DayFacts?,
  incoming: DayFacts,
  isToday: Boolean,
  identical: Boolean,
): PutResult
```

`CapturedDayStore.putDay` is reduced to reading, asking, and writing. The
narrowness is intentional: a stored day carries about fifteen fields, and
passing the whole thing in would invite the decision to start depending on
fields nobody has reasoned about.

Plain JUnit, no Robolectric:

```
./gradlew :usage-stats:testDebugUnitTest      # 15 tests
```

### The finding: a rule nobody was testing

Writing passing tests proves nothing on its own, so each rule was deleted in
turn and the suite re-run. Two of the three behaved as hoped. **Rule 2 did
not:**

```
### Rule 2 removed: an empty re-read may overwrite a measured day
  -> all passed
```

The rule could be deleted entirely and the suite stayed green - including the
test named `a_day_that_has_aged_out_of_androids_window_is_refused`, which was
passing **for the wrong reason**. An aged-out read carries `hadAnyUsage=false`
*and* zero minutes, so rule 3 (`0 < 520`) refuses it first and rule 2 never
runs. The test's name overstated what it covered.

Rule 2 is load-bearing in exactly one situation, which no test described: a day
that carried activity while rounding to **zero** screen minutes - a few app
launches and a glance at the lock screen. Android later forgets the date and
also reports zero. Rule 3 compares `0 < 0`, permits the write, and the day
becomes unknown. Only rule 2 sees that a KNOWN day is about to be replaced by
an UNKNOWN one:

```kotlin
val knownButQuiet = facts(0, hadUsage = true)
val unknown       = facts(0, hadUsage = false)
```

That distinction is the basis of how this whole project treats missing data:
*measured as nothing* and *not measured* are different facts, and one must
never masquerade as the other. It was being enforced by a line of code that
nothing checked.

### Every rule now load-bearing

| Rule deleted | Tests failing |
|---|---|
| 2 - an unknown read never replaces a measured day | 1 |
| 3 - a past day's screen time cannot shrink | 3 |
| 4 - today is always replaced | 2 |
| UNCHANGED collapsed into UPDATED | 3 |

The suite also follows one date through its whole life in
`a_day_survives_its_whole_life_cycle`: created in the morning at 54 minutes,
grown to 520 by midnight, unchanged for a week, then refused twice as Android's
events age out - ending with the original measurement intact, which is the
entire promise of the feature.

### What is still not covered

- **`CapturedDayStore` itself** - the SharedPreferences reads and writes, the
  pending-day ordering, the uploaded flags and pruning. Testing those needs
  Robolectric or an instrumented run. The decision logic was extracted
  precisely because it is where the irreversible risk lives; the storage around
  it fails loudly and recoverably.
- **The TypeScript capture layer.** `capture.ts` and `foreground-capture.ts`
  carry real logic now - the throttle, the accepted-dates handling - and have
  no tests at all. The `accepted_dates` bug in Phase 11 was client-side, and
  `tsc` cannot catch that class of mistake. This is the most obvious remaining
  test gap in the project.
