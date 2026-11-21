# Request-path performance

Route bundles load on demand. Navigation preloads a destination on user intent.
Overview pages avoid requests for inactive tabs, and lookup endpoints batch reads
for the items on screen. The selected-source chat path fetches links by source ID.

## Isolated PostgreSQL measurements

The experiment used PostgreSQL 16 with pgvector, a single application process,
synthetic text uploads, and mocked model calls with 125 ms of simulated latency.
Each workflow created a chat session, uploaded a text file, processed it directly,
read its status, and asked a question against that source. Each row is two batches
at the stated concurrency. The before and after runs used the same recipe.

| Concurrent workflows | Completed | Batch total before → after | Event-loop lag p95 before → after | Health endpoint p95 before → after |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2 of 2 | 2.90 s → 2.59 s | 516 ms → 20 ms | 8 ms → 7 ms |
| 8 | 16 of 16 | 11.67 s → 4.17 s | 1,353 ms → 17 ms | 557 ms → 25 ms |
| 16 | 32 of 32 | 22.09 s → 7.95 s | 1,247 ms → 16 ms | 1,410 ms → 29 ms |

With 100,000 synthetic claim-to-source associations, a lookup returning 20 rows
changed from a sequential scan to a bitmap heap scan after adding the source
index. `EXPLAIN (ANALYZE, BUFFERS)` recorded 11.08 ms and 518 local buffer hits
before the index, and 0.085 ms and 20 hits after it.

For a 25-session page over 100,000 synthetic messages, the grouped count used
the existing session index and returned the same counts as 25 separate queries.
Median database round-trip time per page fell from 22.03 ms to 1.64 ms across
seven paired runs.

Removing event-loop blocking exposed a second bottleneck. Direct ingestion
could occupy all pool connections while checking cancellation on a separate
connection. Direct requests now wait asynchronously for an ingestion slot.
At 16 concurrent requests, ingestion remained the slowest stage at 3.00 s p95,
and observed pool use still reached 15 connections. Shortening the ingestion
transaction and measuring the cancellation checks are candidates for further
testing under a sustained workload.

The browser-style variant submitted sources to the local two-worker dispatcher
and polled batch status every 250 ms. At 16 concurrent workflows, both versions
completed 32 of 32. The batch took 18.64 s before and 15.58 s after. Health
p95 fell from 144 ms to 56 ms, and upload p95 fell from 2.79 s to 0.56 s.
Queue admission still took 1.50 s p95. To improve that path further, measure
job persistence and dispatcher submission separately under sustained load.

These are local ASGI measurements. They exclude network and browser latency,
live provider limits, long documents, and the production Celery/Redis deployment.
