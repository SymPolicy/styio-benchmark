# Soak Regression Template

- **Failure Date:** YYYY-MM-DD
- **Detected By:** local / CI / nightly
- **Related Test:** `StyioSoakSingleThread.*` or `soak_deep_*`
- **Owner:** @owner

## 1. Symptom

- Expected:
- Actual:
- Exit code / signal:

## 2. Reproduction

```bash
# Minimal reproducible command
ctest --test-dir build -R '^soak_deep_...$' --output-on-failure
```

- Environment variables:
  - `STYIO_SOAK_*`:
  - Sanitizer options (if any):
- Platform / compiler:

## 3. Minimization Record

- Source artifact directory: `regressions/<timestamp>-<case>/`
- Minimization command:

```bash
./tools/soak-minimize.sh \
  --styio-root /path/to/styio \
  --test <gtest-filter> \
  --var <iteration-env-var> \
  --low <n> \
  --high <n> \
  --extra-env "K=V;K2=V2"
```

- Min failing threshold:
- Notes about monotonic assumption validity:

## 4. Root Cause

- Subsystem:
- Trigger path:
- Why previous tests missed it:

## 5. Fix

- Fix PR/commit:
- Behavior change summary:
- Risk/tradeoff:

## 6. Regression Coverage

- Added/updated tests:
- CTest command used for verification:
- CI run link / artifact:

## 7. Follow-ups

1.
2.
