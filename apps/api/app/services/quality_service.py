"""How good is this project's memory — and when it is not, why, and what to change.

A dashboard of numbers answers the first question and leaves the second to the reader. This
report is built around the second: every metric that is off produces a *diagnostic* naming
the cause and a concrete fix, derived from evidence the system already has — the stored
decision behind every event (§5b), the memories themselves, the questions people asked, and
the last evaluation run (§26 2.1).

"31% of `support_message` events score below your importance threshold of 0.20" is
actionable. "Coverage 69%" is not.

The event aggregations run in SQL over ``events.outcome`` so the cost grows with the size of
the window, not with the size of the project's history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reader import Reader
from common.settings import get_settings
from common.time import utcnow
from database.models import Project
from database.repositories import EvalRepository
from nlp.concepts import concepts_for
from nlp.tokenize import surface_words

# How close below the consolidation threshold a "create" has to be to count as a probable
# duplicate — a statement that nearly merged.
NEAR_MISS_BAND = 0.10
STALE_DAYS = 90
MIN_SAMPLE = 5
# The language of asking, not of the thing asked about. Their absence from memory is not a
# vocabulary gap — no memory says "wrong", and none should.
QUESTION_WORDS = frozenset(
    {"wrong", "issue", "issues", "problem", "problems", "happening", "happened", "happen", "going",
     "status", "update", "updates", "anything", "something", "recently", "recent", "latest", "customer",
     "customers", "tell", "know", "any", "today", "yesterday", "week", "month", "why", "what",
     "how", "when", "where", "who", "which", "please", "thanks", "current", "currently", "about",
     "should", "would", "could", "experienced", "experiencing", "having", "there", "ever", "still",
     "much", "many", "more", "most", "less", "risk", "doing", "done", "situation", "anyone"}
)


@dataclass(slots=True)
class Diagnostic:
    key: str
    severity: str  # info | warning | critical
    title: str
    detail: str
    fix: dict[str, Any] = field(default_factory=dict)
    examples: list[Any] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "fix": self.fix,
            "examples": self.examples[:5],
        }


class QualityService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        """Numbers are shown to everyone; the few example *words* the report quotes — the
        near-miss pairs — only where this reader may see both memories."""
        self.session = session
        self.reader = Reader(session, cleared=cleared)

    async def report(self, *, project: Project, days: int = 30) -> dict[str, Any]:
        since = utcnow() - timedelta(days=days)
        settings = project.settings or {}
        engine_settings = get_settings()
        threshold = float(settings.get("min_event_importance", engine_settings.memory_min_event_importance))
        consolidation = float(
            settings.get("consolidation_similarity", engine_settings.memory_consolidation_similarity)
        )

        events = await self._events(project.id, since)
        actions = await self._actions(project.id, since, consolidation)
        memories = await self._memories(project.id)
        searches = await self._searches(project.id, since)
        evaluation = await self._evaluation(project.id)

        metrics = {
            "events": events,
            "consolidation": actions,
            "memories": memories,
            "searches": searches,
            "evaluation": evaluation,
            "settings": {"min_event_importance": threshold, "consolidation_similarity": consolidation},
        }
        components = self._components(events, actions, memories, evaluation)
        scored = [component["score"] for component in components if component["score"] is not None]
        diagnostics = self._diagnose(
            events=events,
            actions=actions,
            memories=memories,
            searches=searches,
            evaluation=evaluation,
            threshold=threshold,
            consolidation=consolidation,
        )
        order = {"critical": 0, "warning": 1, "info": 2}
        diagnostics.sort(key=lambda item: order.get(item.severity, 3))
        return {
            "window_days": days,
            "score": round(sum(scored) / len(scored)) if scored else None,
            "components": components,
            "metrics": metrics,
            "diagnostics": [item.as_dict() for item in diagnostics],
            "computed_at": utcnow(),
        }

    # ---------------------------------------------------------------- gather

    async def _events(self, project_id: str, since) -> dict[str, Any]:
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT event_type,
                           status,
                           outcome->>'stop_code' AS stop_code,
                           COALESCE((outcome->>'memory_count')::int, 0) > 0 AS produced,
                           outcome IS NOT NULL AS explained,
                           count(*) AS n,
                           avg(importance) AS importance
                    FROM events
                    WHERE project_id = :project AND created_at >= :since
                    GROUP BY 1, 2, 3, 4, 5
                    """
                ),
                {"project": project_id, "since": since},
            )
        ).all()
        by_type: dict[str, dict[str, Any]] = {}
        totals = {
            "total": 0, "processed": 0, "skipped": 0, "failed": 0, "pending": 0, "produced": 0,
            # Processed events that carry a recorded outcome (§5b). Yield is measured over
            # these only: an event processed before outcomes existed has no record of what
            # it produced, and counting it as "produced nothing" would understate yield.
            "explained": 0,
        }
        for event_type, status, stop_code, produced, explained, count, importance in rows:
            entry = by_type.setdefault(
                event_type,
                {"event_type": event_type, "total": 0, "processed": 0, "produced": 0, "below_threshold": 0,
                 "no_text": 0, "failed": 0, "importance": 0.0},
            )
            count = int(count)
            entry["total"] += count
            entry["importance"] = max(entry["importance"], float(importance or 0.0))
            totals["total"] += count
            status = str(status)
            if status == "processed":
                entry["processed"] += count
                totals["processed"] += count
                if explained:
                    totals["explained"] += count
                if produced:
                    entry["produced"] += count
                    totals["produced"] += count
            elif status == "skipped":
                totals["skipped"] += count
                if stop_code == "below_threshold":
                    entry["below_threshold"] += count
                elif stop_code == "no_text":
                    entry["no_text"] += count
            elif status == "failed":
                entry["failed"] += count
                totals["failed"] += count
            elif status in ("pending", "processing"):
                totals["pending"] += count

        backlog = await self.session.scalar(
            text("SELECT count(*) FROM events WHERE project_id = :project AND status IN ('pending', 'processing')"),
            {"project": project_id},
        )
        totals["backlog"] = int(backlog or 0)
        totals["yield"] = round(totals["produced"] / totals["explained"], 4) if totals["explained"] else None
        return {**totals, "by_type": sorted(by_type.values(), key=lambda item: -item["total"])}

    async def _actions(self, project_id: str, since, consolidation: float) -> dict[str, Any]:
        near = max(0.0, consolidation - NEAR_MISS_BAND)
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT m->>'action' AS action,
                           count(*) AS n,
                           count(*) FILTER (
                               WHERE m->>'action' = 'create'
                                 AND COALESCE((m->>'similarity')::float, 0) >= :near
                           ) AS near_miss
                    FROM events e, jsonb_array_elements(e.outcome->'memories') AS m
                    WHERE e.project_id = :project AND e.created_at >= :since AND e.outcome IS NOT NULL
                    GROUP BY 1
                    """
                ),
                {"project": project_id, "since": since, "near": near},
            )
        ).all()
        counts = {str(action): int(count) for action, count, _ in rows}
        near_miss = sum(int(value) for _, _, value in rows)
        statements = sum(counts.values())
        creates = counts.get("create", 0)
        conflicts = counts.get("conflict", 0) + counts.get("supersede", 0)
        merges = counts.get("merge", 0) + counts.get("update", 0)

        candidates = (
            await self.session.execute(
                text(
                    """
                    SELECT m->>'content', m->>'closest_content', (m->>'similarity')::float,
                           m->>'memory_id', m->>'closest_memory_id'
                    FROM events e, jsonb_array_elements(e.outcome->'memories') AS m
                    WHERE e.project_id = :project AND e.created_at >= :since
                      AND m->>'action' = 'create' AND COALESCE((m->>'similarity')::float, 0) >= :near
                      AND m->>'closest_content' IS NOT NULL
                    ORDER BY (m->>'similarity')::float DESC
                    LIMIT 25
                    """
                ),
                {"project": project_id, "since": since, "near": near},
            )
        ).all()
        # Both halves of a pair are memory text, so a pair is shown only when the reader
        # may see both memories. Over-fetched so that filtering still leaves five.
        visible = await self.reader.visible_memory_ids(
            project_id, {ident for row in candidates for ident in (row[3], row[4]) if ident}
        )
        examples = [
            (content, closest, similarity)
            for content, closest, similarity, memory_id, closest_id in candidates
            if visible is None or (memory_id in visible and closest_id in visible)
        ][:5]
        return {
            "statements": statements,
            "by_action": counts,
            "creates": creates,
            "merges": merges,
            "conflicts": conflicts,
            "near_miss_creates": near_miss,
            "near_miss_rate": round(near_miss / creates, 4) if creates else None,
            "conflict_rate": round(conflicts / statements, 4) if statements else None,
            "merge_rate": round(merges / statements, 4) if statements else None,
            "near_miss_examples": [
                {"content": content, "closest_content": closest, "similarity": round(similarity or 0.0, 3)}
                for content, closest, similarity in examples
            ],
        }

    async def _memories(self, project_id: str) -> dict[str, Any]:
        stale_before = utcnow() - timedelta(days=STALE_DAYS)
        expiring_before = utcnow() + timedelta(days=7)
        row = (
            await self.session.execute(
                text(
                    """
                    SELECT count(*) AS active,
                           avg(confidence) AS confidence,
                           count(*) FILTER (WHERE confidence < 0.5) AS low_confidence,
                           count(*) FILTER (WHERE last_seen_at < :stale) AS stale,
                           count(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at < :expiring) AS expiring,
                           count(*) FILTER (WHERE sensitivity = 'restricted') AS restricted
                    FROM memories
                    WHERE project_id = :project AND status = 'active'
                    """
                ),
                {"project": project_id, "stale": stale_before, "expiring": expiring_before},
            )
        ).one()
        active = int(row.active or 0)
        return {
            "active": active,
            "avg_confidence": round(float(row.confidence), 4) if row.confidence is not None else None,
            "low_confidence": int(row.low_confidence or 0),
            "low_confidence_rate": round(int(row.low_confidence or 0) / active, 4) if active else None,
            "stale": int(row.stale or 0),
            "stale_rate": round(int(row.stale or 0) / active, 4) if active else None,
            "expiring_soon": int(row.expiring or 0),
            "restricted": int(row.restricted or 0),
        }

    async def _searches(self, project_id: str, since) -> dict[str, Any]:
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT query, jsonb_array_length(memory_ids) = 0 AS empty
                    FROM query_logs
                    WHERE project_id = :project AND created_at >= :since AND kind = 'query'
                    ORDER BY created_at DESC
                    LIMIT 500
                    """
                ),
                {"project": project_id, "since": since},
            )
        ).all()
        total = len(rows)
        queries = [query for query, _ in rows]
        empty = sum(1 for _, is_empty in rows if is_empty)
        # Retrieval nearly always returns *something* — recency alone guarantees it — so a
        # question that found nothing is rare and a poor signal. The real one is a question
        # asked in words no memory uses: it gets an answer, just not from anything about
        # what was asked.
        unknown = await self._unknown_terms(project_id, queries)
        affected = [query for query in queries if any(word in unknown for word in surface_words(query))]
        return {
            "total": total,
            "empty": empty,
            "with_unknown_terms": len(affected),
            "unknown_rate": round(len(affected) / total, 4) if total else None,
            "unknown_terms": sorted(unknown, key=lambda word: -unknown[word])[:12],
            "examples": list(dict.fromkeys(affected))[:5],
        }

    async def _unknown_terms(self, project_id: str, queries: list[str]) -> dict[str, int]:
        """Words people searched for that no active memory contains — vocabulary gaps."""
        counts: dict[str, int] = {}
        for query in queries:
            for word in set(surface_words(query)):
                if len(word) >= 3 and not word.isdigit() and word not in QUESTION_WORDS:
                    counts[word] = counts.get(word, 0) + 1
        if not counts:
            return {}
        # A word the concept layer understands is not a gap when a memory is about that
        # concept: "churning" reaches a memory that says "cancel" through `cancellation`.
        known_concepts = set(
            (
                await self.session.execute(
                    text(
                        "SELECT DISTINCT unnest(concepts) FROM memories "
                        "WHERE project_id = :project AND status = 'active' AND concepts IS NOT NULL"
                    ),
                    {"project": project_id},
                )
            ).scalars()
        )
        counts = {
            word: count
            for word, count in counts.items()
            if not (set(concepts_for(word)) & known_concepts)
        }
        if not counts:
            return {}
        words = sorted(counts, key=lambda word: -counts[word])[:200]
        missing = (
            await self.session.execute(
                text(
                    """
                    SELECT word FROM unnest(CAST(:words AS text[])) AS word
                    WHERE NOT EXISTS (
                        SELECT 1 FROM memories
                        WHERE project_id = :project AND status = 'active'
                          AND to_tsvector('english', content) @@ plainto_tsquery('english', word)
                    )
                    """
                ),
                {"project": project_id, "words": words},
            )
        ).scalars()
        return {word: counts[word] for word in missing}

    async def _evaluation(self, project_id: str) -> dict[str, Any] | None:
        repo = EvalRepository(self.session)
        sets = await repo.list_sets(project_id)
        latest = None
        for eval_set in sets:
            run = await repo.latest_succeeded(eval_set.id)
            if run is not None and (latest is None or run.created_at > latest[1].created_at):
                latest = (eval_set, run)
        if latest is None:
            return None
        eval_set, run = latest
        return {
            "set_id": eval_set.id,
            "set": eval_set.name,
            "run_id": run.id,
            "ran_at": run.finished_at,
            "recall_at_5": (run.metrics or {}).get("recall", {}).get("@5"),
            "mrr": (run.metrics or {}).get("mrr"),
            "citation_hit_rate": (run.metrics or {}).get("citation_hit_rate"),
            "regressed": bool((run.comparison or {}).get("regressed")),
        }

    # ---------------------------------------------------------------- score

    @staticmethod
    def _components(events, actions, memories, evaluation) -> list[dict[str, Any]]:
        """The score's parts, each 0–100 and each with the number it came from."""

        def pct(value: float | None) -> int | None:
            return None if value is None else max(0, min(100, round(value * 100)))

        return [
            {
                "key": "yield",
                "label": "Extraction yield",
                "score": pct(events["yield"]),
                "detail": "Share of events that cleared the threshold and produced a memory.",
            },
            {
                "key": "duplicates",
                "label": "Duplicate control",
                "score": pct(None if actions["near_miss_rate"] is None else 1 - actions["near_miss_rate"]),
                "detail": "Share of new memories that were not a near-miss of an existing one.",
            },
            {
                "key": "consistency",
                "label": "Consistency",
                "score": pct(None if actions["conflict_rate"] is None else 1 - actions["conflict_rate"]),
                "detail": "Share of statements that did not contradict what was already known.",
            },
            {
                "key": "confidence",
                "label": "Confidence",
                "score": pct(memories["avg_confidence"]),
                "detail": "Average confidence of active memories.",
            },
            {
                "key": "freshness",
                "label": "Freshness",
                "score": pct(None if memories["stale_rate"] is None else 1 - memories["stale_rate"]),
                "detail": f"Share of active memories seen in the last {STALE_DAYS} days.",
            },
            {
                "key": "retrieval",
                "label": "Retrieval",
                "score": pct(evaluation["recall_at_5"]) if evaluation else None,
                "detail": "Recall@5 of the latest evaluation run — unscored until one exists.",
            },
        ]

    # ------------------------------------------------------------- diagnose

    def _diagnose(self, *, events, actions, memories, searches, evaluation, threshold, consolidation) -> list[Diagnostic]:
        found: list[Diagnostic] = []

        for entry in events["by_type"]:
            share = entry["below_threshold"] / entry["total"] if entry["total"] else 0
            if entry["below_threshold"] >= MIN_SAMPLE and share >= 0.25:
                found.append(
                    Diagnostic(
                        key="threshold_filtering",
                        severity="warning",
                        title=f"{round(share * 100)}% of `{entry['event_type']}` events are never read",
                        detail=(
                            f"{entry['below_threshold']} of {entry['total']} `{entry['event_type']}` events in "
                            f"the window scored below this project's importance threshold of {threshold:.2f}, "
                            "so they are stored but never become memory. If this event type carries "
                            "something worth remembering, give it an importance override."
                        ),
                        fix={
                            "action": "set",
                            "setting": f"event_importance.{entry['event_type']}",
                            "value": round(max(threshold + 0.05, 0.3), 2),
                        },
                        examples=[entry],
                    )
                )
            if entry["no_text"] >= 1:
                found.append(
                    Diagnostic(
                        key="unreadable_payloads",
                        severity="warning",
                        title=f"`{entry['event_type']}` events have nothing to read",
                        detail=(
                            f"{entry['no_text']} `{entry['event_type']}` events had no readable values "
                            "at all — empty payloads, or only the reserved keys importance, metadata "
                            "and _meta. Put the content in an ordinary field."
                        ),
                        fix={"action": "preview", "event_type": entry["event_type"]},
                        examples=[entry],
                    )
                )

        if actions["creates"] >= 10 and (actions["near_miss_rate"] or 0) >= 0.15:
            target = round(max(0.3, consolidation - 0.05), 2)
            found.append(
                Diagnostic(
                    key="near_duplicates",
                    severity="warning",
                    title=f"{round(actions['near_miss_rate'] * 100)}% of new memories nearly merged",
                    detail=(
                        f"{actions['near_miss_creates']} of {actions['creates']} memories created in the "
                        f"window were within {NEAR_MISS_BAND:.2f} of the consolidation threshold "
                        f"({consolidation:.2f}) — probably the same thing said differently. Lowering "
                        "the threshold merges them; run an evaluation before and after."
                    ),
                    fix={"action": "set", "setting": "consolidation_similarity", "value": target},
                    examples=actions["near_miss_examples"],
                )
            )

        if actions["statements"] >= 10 and (actions["conflict_rate"] or 0) >= 0.2:
            found.append(
                Diagnostic(
                    key="frequent_conflicts",
                    severity="info",
                    title=f"{round(actions['conflict_rate'] * 100)}% of statements contradicted memory",
                    detail=(
                        "A high contradiction rate is either a customer whose situation really is "
                        "changing fast, or events that report state inconsistently. Check the "
                        "superseded memories in the version history."
                    ),
                    fix={"action": "review", "where": "memories"},
                )
            )

        if events["failed"]:
            found.append(
                Diagnostic(
                    key="failed_events",
                    severity="critical",
                    title=f"{events['failed']} events failed and never became memory",
                    detail="They are kept, with the error. Once the cause is fixed, retrying them is safe.",
                    fix={"action": "retry_failed"},
                )
            )

        if events["backlog"] > 100:
            found.append(
                Diagnostic(
                    key="backlog",
                    severity="warning",
                    title=f"{events['backlog']} events are waiting to be processed",
                    detail="The worker is falling behind. Check that it is running and add capacity if it is.",
                    fix={"action": "scale_worker"},
                )
            )

        if memories["active"] >= 20 and (memories["low_confidence_rate"] or 0) >= 0.3:
            found.append(
                Diagnostic(
                    key="low_confidence",
                    severity="info",
                    title=f"{round(memories['low_confidence_rate'] * 100)}% of memories are low-confidence",
                    detail=(
                        "Confidence rises with repeated evidence and with feedback. Confirming or "
                        "correcting memories from the dashboard teaches it which ones are right."
                    ),
                    fix={"action": "review", "where": "memories"},
                )
            )

        if memories["active"] >= 20 and (memories["stale_rate"] or 0) >= 0.4:
            found.append(
                Diagnostic(
                    key="stale_memories",
                    severity="info",
                    title=f"{round(memories['stale_rate'] * 100)}% of memories have not been seen in {STALE_DAYS} days",
                    detail="Stale memories still answer questions. Check the retention and decay settings.",
                    fix={"action": "review", "where": "settings.retention"},
                )
            )

        if searches["total"] >= 10 and (searches["unknown_rate"] or 0) >= 0.2:
            terms = searches["unknown_terms"]
            found.append(
                Diagnostic(
                    key="vocabulary_gaps",
                    severity="warning",
                    title=f"{round(searches['unknown_rate'] * 100)}% of questions use words no memory contains",
                    detail=(
                        f"{searches['with_unknown_terms']} of {searches['total']} questions asked about "
                        f"{', '.join(terms[:6])} — words that appear in no memory. Those questions are "
                        "answered from whatever else matched. If the words mean something your memories "
                        "say differently, add them to the vocabulary as synonyms."
                    ),
                    fix={"action": "add_vocabulary", "terms": terms[:6]},
                    examples=searches["examples"],
                )
            )

        if evaluation is None:
            found.append(
                Diagnostic(
                    key="no_evaluation",
                    severity="info",
                    title="Retrieval has never been measured",
                    detail=(
                        "Create an evaluation set — questions whose right answers you know — so a change "
                        "to vocabulary or settings is measured rather than guessed."
                    ),
                    fix={"action": "create_evaluation"},
                )
            )
        else:
            if evaluation["recall_at_5"] is not None and evaluation["recall_at_5"] < 0.8:
                found.append(
                    Diagnostic(
                        key="weak_retrieval",
                        severity="warning",
                        title=f"Retrieval finds the right memory in the top 5 only {round(evaluation['recall_at_5'] * 100)}% of the time",
                        detail=f"From the latest run of “{evaluation['set']}”. Its misses list shows which questions fail.",
                        fix={"action": "open_evaluation", "run_id": evaluation["run_id"]},
                    )
                )
            if evaluation["regressed"]:
                found.append(
                    Diagnostic(
                        key="retrieval_regressed",
                        severity="critical",
                        title="The latest evaluation run regressed",
                        detail="At least one question that used to be answered no longer is.",
                        fix={"action": "open_evaluation", "run_id": evaluation["run_id"]},
                    )
                )
        return found
