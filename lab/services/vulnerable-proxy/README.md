# vulnerable-proxy (placeholder)

The M2 transitive-escape experiment is implemented in
[../fetch-service](../fetch-service) instead -- a minimal internal
service with one deliberately dangerous endpoint (`GET /fetch?url=...`).
This directory stays reserved for a more elaborate SSRF-style service
later (e.g. one with its own internal-only targets to probe), if a
richer scenario than "fetch an arbitrary URL" turns out to be useful.

See the milestones in the top-level [README](../../../README.md).
