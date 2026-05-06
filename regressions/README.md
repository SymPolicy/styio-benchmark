# Soak Regression Artifacts

This directory stores minimized failing soak cases produced by:

```bash
./tools/soak-minimize.sh --styio-root /path/to/styio ...
```

Each case folder contains:

- `CASE.md`: context and follow-up checklist
- `env.txt`: minimized environment snapshot
- `repro.sh`: one-command local replay
- `*.log`: failure outputs captured during minimization

When a regression is fixed, keep the artifact folder and link the fix commit in `CASE.md`.
