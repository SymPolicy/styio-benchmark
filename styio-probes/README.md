# Styio Probe Sources

This directory owns the C++ probe sources migrated out of the Styio compiler
checkout.

The probe target names are still registered by the Styio checkout so existing
CTest labels and benchmark routes keep working:

- `styio_soak_test`
- `styio_task_scheduler_perf_test`

These files may include Styio private headers because they are compiled by a
matching Styio source checkout through `benchmark/CMakeLists.txt`. Keep route
logic, reports, baselines, and cross-runtime comparisons elsewhere in this
repository.
