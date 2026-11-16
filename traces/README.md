# Trace logs

## Team runs (Assignment 2)

Three files, one per service. Each line is one JSON object with these fields:
`ts, service, run_id, request_id, step, direction (in|out), endpoint_or_tool, status, latency_ms, attempt, tokens`
(a few lines add `detail`). `in` is a request the service received; `out` is a call it made (to a worker, to the app,
to a model, to a web page). Latency is measured by the caller. Tokens, keys and passwords are never written.

| File | Written by |
| --- | --- |
| `orchestrator.jsonl` | the orchestrator |
| `researcher.jsonl` | the researcher |
| `verifier.jsonl` | the verifier. On Render it keeps no files; run `python scripts/extract_trace.py render-log.txt` on a copy of Render's log to make this file. |

Follow one request through all three logs (`request_id` comes from a line in `orchestrator.jsonl`):

```
grep -h "<request_id>" traces/*.jsonl          (Windows: findstr /s "<request_id>" traces\*.jsonl)
```

Summarize the logs: `python -m orchestrator stats` (round trips, time and tokens per service).

## Single-agent runs (Assignment 1B)

`run1.jsonl` and `run2.jsonl` are the traces of the two 1B tracker runs (a model call, a tool call, an HTTP round trip,
a retry, a note or a budget stop per line, with a slightly different set of fields). They go with `reports/run1.md` and
`reports/run2.md`, and are the baseline for `ORCHESTRATION.md` question 5.
