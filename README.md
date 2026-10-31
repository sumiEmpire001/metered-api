# Metered API

A local authenticated API for fixed-window usage accounting, per-client quotas, idempotent charge events and paginated usage exports. Built for Lord_Sumi's portfolio.

## Try it

Python 3.10+, standard library only. Run `python -m unittest discover -s tests -v` and `python demo.py`, or use **Actions → Tests and demo → Run workflow**. The temporary demo uses synthetic client IDs and does not print an API key.

Create a client on your own machine with `python -m metered_api --db meter.db create-client demo --quota 100`. The returned random API key is shown once; keep it outside the repository. Start the loopback server with `python -m metered_api --db meter.db serve --port 8080`.

## HTTP routes

Every route requires `Authorization: Bearer YOUR_KEY`. POST /charge accepts JSON fields event_id, units (default 1) and label (default request). GET /usage returns the current window, used units, quota, remaining units and reset timestamp. GET /events supports limit (1..1000) and offset. GET /export.csv returns at most 1000 events, starting at the requested offset. Repeat exports with increasing offsets for more data. Unknown charge fields are rejected, so a request cannot select a different client identity.

## Consistency and data

Random high-entropy API keys are stored only as SHA-256 digests. Disabled clients cannot authenticate or charge. Administrators configure clients through the local library/CLI boundary; there are no remote administration routes.

SQLite write transactions combine the quota check, usage increment and immutable event insertion. Event IDs are unique per client across all windows. An identical retry returns the original receipt without charging the current window; conflicting ID reuse fails. Window boundaries are fixed epoch multiples (86400 seconds by default), not rolling time intervals. The window size is persisted and incompatible reopening is rejected.

Reducing a quota does not erase already used units: remaining becomes zero and future charges are refused until the next window. CSV export prefixes formula-like text cells with an apostrophe; exports may therefore differ from raw labels. The database retains labels and historical events locally without encryption or automatic deletion.

## Verification

Twelve local tests passed on 31 October 2026: quota boundaries, concurrent increments and duplicates, exact window rollover, client isolation, hashed/revoked credentials, quota reduction, CSV protection, restart/pagination, invalid input and real HTTP authentication/charge/export requests. CI runs the same suite and a synthetic demonstration. Version-matrix runs are not counted as separate tests.

## Limits

The HTTP service binds to 127.0.0.1 and uses Python's development server. No TLS termination, network abuse protection, billing, payment collection, API-key rotation interface, distributed counter or retention cleanup is implemented. Wall-clock changes affect buckets. Library calls taking client IDs are trusted internal operations; remote callers must use the authenticated HTTP interface. Registering an API key does not prove a real person's identity. Export memory is bounded to a 1000-row page; high-volume production performance has not been benchmarked.

MIT license; see LICENSE.
