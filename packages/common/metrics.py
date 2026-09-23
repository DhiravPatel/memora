"""Prometheus metrics.

Exposed by the API at ``/metrics``; the worker updates the same collectors so the
processing pipeline is observable end to end.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

api_requests = Counter(
    "api_requests_total", "API requests", ["method", "path", "status"]
)
api_latency = Histogram(
    "api_request_duration_seconds", "API latency", ["method", "path"], buckets=_LATENCY_BUCKETS
)
event_processing_latency = Histogram(
    "event_processing_duration_seconds", "Event processing latency", buckets=_LATENCY_BUCKETS
)
events_processed = Counter("events_processed_total", "Processed events", ["status"])
queue_depth = Gauge("queue_depth", "Pending jobs in the memory queue")
ai_latency = Histogram(
    "ai_request_duration_seconds", "AI provider latency", ["provider", "operation"],
    buckets=_LATENCY_BUCKETS,
)
ai_tokens = Counter("ai_tokens_total", "AI tokens used", ["provider", "kind"])
ai_errors = Counter("ai_errors_total", "AI provider errors", ["provider", "operation"])
embedding_latency = Histogram(
    "embedding_duration_seconds", "Embedding latency", ["provider"], buckets=_LATENCY_BUCKETS
)
database_latency = Histogram(
    "database_operation_duration_seconds", "Database latency", ["operation"],
    buckets=_LATENCY_BUCKETS,
)
retrieval_latency = Histogram(
    "retrieval_duration_seconds", "Retrieval latency", ["strategy"], buckets=_LATENCY_BUCKETS
)
memories_created = Counter("memories_created_total", "Memories created", ["type"])
memories_updated = Counter("memories_updated_total", "Memories updated", ["reason"])


@contextmanager
def observe(histogram: Histogram, **labels: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        target = histogram.labels(**labels) if labels else histogram
        target.observe(time.perf_counter() - started)
