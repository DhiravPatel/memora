"""Predictive signals and recommendations, for one customer and for a portfolio.

Like health, this is derived from the same rows the API already serves, so a forecast can
never disagree with the memories shown next to it. The only stored part is the daily
snapshot, which exists so "declining" can be measured against a real earlier reading
rather than guessed from the current state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reader import Reader
from common.enums import Trajectory
from common.time import days_ago, ensure_utc
from database.models import Customer, Project, SignalSnapshot
from database.repositories import (
    CustomerRepository,
    EventRepository,
    GoalRepository,
    MemoryRepository,
    SignalSnapshotRepository,
)
from memory_engine.analytics import (
    ActivityWindow,
    Baseline,
    HealthScore,
    Recommendation,
    SignalReport,
    compute_signals,
    recommend,
    summarise,
)
from memory_engine.analytics import compute as compute_health
from memory_engine.analytics.signals import WINDOW_DAYS
from memory_engine.engine import MemoryEngine

PORTFOLIO_LIMIT = 200
SERIES_DAYS = 90


@dataclass(slots=True)
class CustomerSignals:
    customer_id: str
    external_id: str
    name: str | None
    report: SignalReport
    health_score: float
    series: list[SignalSnapshot]

    def as_dict(self) -> dict[str, object]:
        return {
            "customer_id": self.customer_id,
            "external_id": self.external_id,
            "name": self.name,
            "health_score": round(self.health_score, 1),
            "series": [
                {
                    "captured_on": snapshot.captured_on,
                    "health_score": round(float(snapshot.health_score), 1),
                    "churn_risk": round(float(snapshot.churn_risk), 3),
                    "expansion_score": round(float(snapshot.expansion_score), 3),
                    "trajectory": snapshot.trajectory,
                }
                for snapshot in self.series
            ],
            **self.report.as_dict(),
        }


@dataclass(slots=True)
class Portfolio:
    """A filtered, ranked list plus counts across *everyone*.

    The counts deliberately ignore the filter: a header that said "0 improving" because
    you were looking at the declining list would be answering a question nobody asked.
    """

    customers: list[CustomerSignals]
    counts: dict[str, int]


class SignalService:
    def __init__(self, session: AsyncSession, *, cleared: bool | None = None) -> None:
        """Pass ``cleared`` for a caller-facing service. The forecast is still computed over
        everything — like health, it is a statement about the customer — but the words it
        quotes from memories or goals the caller may not see are withheld (§26 3.1)."""
        self.session = session
        self.reader = None if cleared is None else Reader(session, cleared=cleared)
        self.customers = CustomerRepository(session)
        self.memories = MemoryRepository(session)
        self.events = EventRepository(session)
        self.goals = GoalRepository(session)
        self.snapshots = SignalSnapshotRepository(session)

    async def for_customer(
        self, *, project: Project, customer: Customer, include_series: bool = True
    ) -> CustomerSignals:
        report, health = await self._report(project=project, customer=customer)
        series: list[SignalSnapshot] = []
        if include_series:
            series = await self.snapshots.series(
                project_id=project.id,
                customer_id=customer.id,
                since=days_ago(SERIES_DAYS).date(),
            )
        if self.reader is not None:
            report = await self.reader.report(project.id, report)
        return CustomerSignals(
            customer_id=customer.id,
            external_id=customer.external_id,
            name=customer.name,
            report=report,
            health_score=health.score,
            series=series,
        )

    async def recommendations(
        self, *, project: Project, customer: Customer
    ) -> tuple[list[Recommendation], SignalReport]:
        report, health = await self._report(project=project, customer=customer)
        goals, _ = await self.goals.list(
            project_id=project.id, customer_id=customer.id, limit=50
        )
        memories, _ = await self.memories.list(
            project_id=project.id, customer_id=customer.id, limit=200
        )
        actions = recommend(
            memories=[MemoryEngine._to_view(memory) for memory in memories],
            report=report,
            goals=[MemoryEngine._goal_snapshot(goal) for goal in goals],
            health_score=health.score,
            customer_name=customer.name,
        )
        if self.reader is not None:
            actions = await self.reader.recommendations(project.id, actions)
            report = await self.reader.report(project.id, report)
        return actions, report

    async def portfolio(
        self,
        *,
        project: Project,
        trajectories: tuple[str, ...] = (),
        limit: int = 50,
    ) -> Portfolio:
        """Forecast every customer in a bounded number of queries, riskiest first."""
        customers, _ = await self.customers.list(project_id=project.id, limit=PORTFOLIO_LIMIT)
        if not customers:
            return Portfolio(customers=[], counts=_empty_counts())

        customer_ids = [customer.id for customer in customers]
        memories_by_customer = await self.memories.active_for_customers(
            project_id=project.id, customer_ids=customer_ids
        )
        windows = await self.events.window_counts_by_customer(
            project_id=project.id,
            customer_ids=customer_ids,
            boundary=days_ago(WINDOW_DAYS),
            since=days_ago(WINDOW_DAYS * 2),
        )
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=customer_ids
        )
        goals_by_customer = await self.goals.for_customers(
            project_id=project.id, customer_ids=customer_ids
        )
        baselines = await self.snapshots.baselines_for_customers(
            project_id=project.id,
            customer_ids=customer_ids,
            on_or_before=days_ago(WINDOW_DAYS).date(),
        )

        weights = (project.settings or {}).get("health_weights")
        results: list[CustomerSignals] = []
        for customer in customers:
            views = [
                MemoryEngine._to_view(memory)
                for memory in memories_by_customer.get(customer.id, [])
            ]
            recent, prior = windows.get(customer.id, (0, 0))
            health = compute_health(
                memories=views,
                event_count=recent + prior,
                last_event_at=customer.last_event_at,
                distinct_features=features.get(customer.id, 0),
                weights=weights,
            )
            report = compute_signals(
                memories=views,
                activity=ActivityWindow(
                    recent_events=recent,
                    prior_events=prior,
                    last_event_at=customer.last_event_at,
                    distinct_features=features.get(customer.id, 0),
                ),
                goals=[
                    MemoryEngine._goal_snapshot(goal)
                    for goal in goals_by_customer.get(customer.id, [])
                ],
                health_score=health.score,
                baseline=_baseline(baselines.get(customer.id)),
            )
            results.append(
                CustomerSignals(
                    customer_id=customer.id,
                    external_id=customer.external_id,
                    name=customer.name,
                    report=report,
                    health_score=health.score,
                    series=[],
                )
            )

        # Declining first, then by churn risk: the order somebody would work the list in.
        order = {
            str(Trajectory.DECLINING): 0,
            str(Trajectory.STEADY): 1,
            str(Trajectory.IMPROVING): 2,
        }
        results.sort(
            key=lambda item: (order.get(str(item.report.trajectory), 1), -item.report.churn_risk)
        )
        counts = trajectory_counts(results)
        if trajectories:
            results = [
                item for item in results if str(item.report.trajectory) in trajectories
            ]
        results = results[:limit]
        if self.reader is not None:
            shaped = await self.reader.reports(project.id, [item.report for item in results])
            for item, report in zip(results, shaped, strict=True):
                item.report = report
        return Portfolio(customers=results, counts=counts)

    async def record_snapshot(self, *, project: Project, customer: Customer) -> SignalReport:
        """Compute and store today's reading. Used by the nightly job."""
        report, health = await self._report(project=project, customer=customer)
        await self.snapshots.record(
            project_id=project.id,
            customer_id=customer.id,
            health_score=health.score,
            churn_risk=report.churn_risk,
            expansion_score=report.expansion_score,
            trajectory=str(report.trajectory),
            signals=[
                {"key": signal.key, "direction": str(signal.direction), "strength": signal.strength}
                for signal in report.signals
            ],
            at=report.computed_at,
        )
        return report

    async def _report(
        self, *, project: Project, customer: Customer
    ) -> tuple[SignalReport, HealthScore]:
        memories, _ = await self.memories.list(
            project_id=project.id, customer_id=customer.id, limit=200
        )
        views = [MemoryEngine._to_view(memory) for memory in memories]
        windows = await self.events.window_counts_by_customer(
            project_id=project.id,
            customer_ids=[customer.id],
            boundary=days_ago(WINDOW_DAYS),
            since=days_ago(WINDOW_DAYS * 2),
        )
        recent, prior = windows.get(customer.id, (0, 0))
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        goals, _ = await self.goals.list(
            project_id=project.id, customer_id=customer.id, limit=50
        )
        baseline = await self.snapshots.baseline(
            project_id=project.id,
            customer_id=customer.id,
            on_or_before=days_ago(WINDOW_DAYS).date(),
        )
        health = compute_health(
            memories=views,
            event_count=recent + prior,
            last_event_at=customer.last_event_at,
            distinct_features=features.get(customer.id, 0),
            weights=(project.settings or {}).get("health_weights"),
        )
        report = compute_signals(
            memories=views,
            activity=ActivityWindow(
                recent_events=recent,
                prior_events=prior,
                last_event_at=customer.last_event_at,
                distinct_features=features.get(customer.id, 0),
            ),
            goals=[MemoryEngine._goal_snapshot(goal) for goal in goals],
            health_score=health.score,
            baseline=_baseline(baseline),
        )
        return report, health

    @staticmethod
    def summarise(recommendations: list[Recommendation]) -> str:
        return summarise(recommendations)


def _baseline(snapshot: SignalSnapshot | None) -> Baseline | None:
    if snapshot is None:
        return None
    return Baseline(
        health_score=float(snapshot.health_score),
        churn_risk=float(snapshot.churn_risk),
        captured_at=ensure_utc(snapshot.captured_at),
    )


def _empty_counts() -> dict[str, int]:
    return {"improving": 0, "steady": 0, "declining": 0}


def trajectory_counts(results: list[CustomerSignals]) -> dict[str, int]:
    counts = _empty_counts()
    for item in results:
        counts[str(item.report.trajectory)] = counts.get(str(item.report.trajectory), 0) + 1
    return counts


def since_date(days: int) -> date:
    return days_ago(days).date()
