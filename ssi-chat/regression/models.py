from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SearchMode = Literal["events", "instructions", "payments", "all"]

# Primary retrieval path the answer is expected to use (vector still runs in parallel except eligibility).
RetrievalStrategy = Literal["deterministic", "graph", "vector", "eligibility"]


class SeedStep(BaseModel):
    action: str
    count: int | None = None


class SeedWaitConfig(BaseModel):
    min_security_events: int = 1
    min_qdrant_points: int = 1
    timeout_seconds: int = 180
    poll_interval_seconds: float = 3.0


class SeedConfig(BaseModel):
    steps: list[SeedStep] = Field(default_factory=list)
    wait: SeedWaitConfig = Field(default_factory=SeedWaitConfig)


class ExpectConfig(BaseModel):
    min_answer_length: int = 1
    answer_contains_any: list[str] = Field(default_factory=list)
    answer_contains_all: list[str] = Field(default_factory=list)
    answer_not_contains: list[str] = Field(default_factory=list)
    answer_has_number: bool = False
    min_sources: int = 0
    min_graph_rows: int = 0
    requires_cypher: bool = False
    requires_context: list[str] = Field(default_factory=list)
    skip_if_missing_context: bool = True


class RegressionCase(BaseModel):
    id: str
    mode: SearchMode
    retrieval: RetrievalStrategy = Field(
        description=(
            "Primary engine for the answer: deterministic (Neo4j formatter, no LLM synthesis), "
            "graph (Neo4j planned/LLM Cypher authoritative), vector (Qdrant dense/BM25 primary), "
            "eligibility (live OPA via authorization-service, no Qdrant)."
        ),
    )
    question: str
    tags: list[str] = Field(default_factory=list)
    expect: ExpectConfig = Field(default_factory=ExpectConfig)


class RegressionSuite(BaseModel):
    version: int = 1
    seed: SeedConfig = Field(default_factory=SeedConfig)
    cases: list[RegressionCase]


class CaseResult(BaseModel):
    id: str
    mode: str
    question: str
    passed: bool
    skipped: bool = False
    reason: str = ""
    answer_preview: str = ""
    sources: int = 0
    graph_rows: int = 0
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    tags: list[str] = Field(default_factory=list)
    retrieval: RetrievalStrategy | None = None


class SuiteResult(BaseModel):
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    cases: list[CaseResult] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
