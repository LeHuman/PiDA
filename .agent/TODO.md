# TODO

The following items were migrated from the previous server goal document.
Their status has not been revalidated against the current implementation.

- Verify parameter/input/output parsing, `INPUT_*` injection, output-file
  handling, typed coercion, per-action timeouts, per-group queueing, and
  `/api/schema` against the current `pipeline.py` and tests.
- Verify server behavior against the documented wire contract, then remove
  any retired hard-coded device routes and imports if they remain.
- Reconcile the detailed [server design notes](NOTES.md) with the
  current code and remove completed or obsolete items from this list.
