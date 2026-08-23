"""Versioned fake fixtures for the P9 planning-quality baseline."""

from __future__ import annotations

from typing import Any

from src.evaluation.planning_quality import PlanningQualityCase, PlanningQualityDataset


PLANNING_QUALITY_DATASET = PlanningQualityDataset(
    dataset_id="researchos_planning_quality",
    version="p9.v1",
    cases=[
        PlanningQualityCase(
            case_id="ai-regulation-comparison",
            query="Compare AI regulation across jurisdictions",
            objective_facets=["jurisdiction", "risk", "implementation"],
            outline_facets=["context", "jurisdiction", "risk", "implementation"],
        ),
        PlanningQualityCase(
            case_id="urban-climate-adaptation",
            query="Assess urban climate adaptation options",
            objective_facets=["hazard", "adaptation", "equity"],
            outline_facets=["hazard", "adaptation", "equity"],
        ),
        PlanningQualityCase(
            case_id="semiconductor-supply-chain",
            query="Evaluate semiconductor supply-chain resilience",
            objective_facets=["supplier", "risk", "mitigation"],
            outline_facets=["supplier", "risk", "mitigation"],
        ),
        PlanningQualityCase(
            case_id="clinical-evidence-translation",
            query="Translate clinical evidence into practice",
            objective_facets=["evidence", "workflow", "outcome"],
            outline_facets=["evidence", "workflow", "outcome"],
        ),
        PlanningQualityCase(
            case_id="enterprise-ai-evaluation",
            query="Evaluate enterprise AI systems",
            objective_facets=["capability", "safety", "procurement"],
            outline_facets=["capability", "safety", "procurement"],
        ),
        PlanningQualityCase(
            case_id="renewable-grid-integration",
            query="Plan renewable grid integration",
            objective_facets=["generation", "grid", "storage"],
            outline_facets=["generation", "grid", "storage"],
        ),
    ],
)


# These frozen fake payloads record the P9 before/after comparison. The
# "before" examples intentionally mirror the observed weak planning pattern:
# generic query purposes and dimensions that never reach the outline. The
# "after" examples are returned through the actual Planner in tests, so its
# prompt and normalization remain part of the tested path.
P9_BASELINE_PLAN_PAYLOADS: dict[str, dict[str, Any]] = {
    "ai-regulation-comparison": {
        "topic": "Compare AI regulation across jurisdictions",
        "objectives": ["Understand AI regulation risk"],
        "search_queries": [
            {"query": "AI regulation overview", "purpose": "Research the topic"},
            {"query": "AI regulation comparison", "purpose": "Research the topic"},
            {"query": "AI regulation rules", "purpose": "Research the topic"},
        ],
        "report_outline": ["Context", "Conclusion"],
    },
    "urban-climate-adaptation": {
        "topic": "Assess urban climate adaptation options",
        "objectives": ["Understand urban adaptation"],
        "search_queries": [
            {"query": "urban climate adaptation", "purpose": "Research the topic"},
            {"query": "urban climate options", "purpose": "Research the topic"},
            {"query": "urban resilience", "purpose": "Research the topic"},
        ],
        "report_outline": ["Overview", "Adaptation options"],
    },
    "semiconductor-supply-chain": {
        "topic": "Evaluate semiconductor supply-chain resilience",
        "objectives": ["Assess supplier diversity", "Analyze risk exposure", "Compare mitigation options"],
        "search_queries": [
            {"query": "semiconductor supplier comparison", "purpose": "comparison: Compare suppliers"},
            {"query": "semiconductor supply-chain risk", "purpose": "risk_limitations: Assess risk"},
            {"query": "semiconductor mitigation implementation", "purpose": "implementation: Review mitigation"},
        ],
        "report_outline": ["Supplier landscape", "Risk exposure", "Mitigation options"],
    },
    "clinical-evidence-translation": {
        "topic": "Translate clinical evidence into practice",
        "objectives": ["Review clinical evidence", "Understand translation"],
        "search_queries": [
            {"query": "clinical evidence translation", "purpose": "Research the topic"},
            {"query": "clinical practice evidence", "purpose": "Research the topic"},
            {"query": "clinical adoption", "purpose": "Research the topic"},
        ],
        "report_outline": ["Clinical evidence", "Conclusion"],
    },
    "enterprise-ai-evaluation": {
        "topic": "Evaluate enterprise AI systems",
        "objectives": ["Evaluate AI capability", "Review safety"],
        "search_queries": [
            {"query": "enterprise AI evaluation", "purpose": "Research the topic"},
            {"query": "enterprise AI safety", "purpose": "Research the topic"},
            {"query": "enterprise AI procurement", "purpose": "Research the topic"},
        ],
        "report_outline": ["Capability evaluation", "Safety review", "Conclusion"],
    },
    "renewable-grid-integration": {
        "topic": "Plan renewable grid integration",
        "objectives": ["Understand renewable generation", "Explore grid integration"],
        "search_queries": [
            {"query": "renewable grid integration", "purpose": "Research the topic"},
            {"query": "renewable generation grid", "purpose": "Research the topic"},
            {"query": "renewable grid storage", "purpose": "Research the topic"},
        ],
        "report_outline": ["Generation and grid context", "Conclusion"],
    },
}


P9_IMPROVED_PLAN_PAYLOADS: dict[str, dict[str, Any]] = {
    "ai-regulation-comparison": {
        "topic": "Compare AI regulation across jurisdictions",
        "objectives": ["Compare jurisdiction approaches", "Assess risk classifications", "Plan implementation implications"],
        "search_queries": [
            {"query": "AI regulation jurisdiction comparison", "purpose": "Compare policy approaches"},
            {"query": "AI regulation official risk classification", "purpose": "Review primary rules"},
            {"query": "AI regulation implementation guidance", "purpose": "Identify adoption implications"},
        ],
        "report_outline": ["Context", "Jurisdiction comparison", "Risk classifications", "Implementation implications"],
    },
    "urban-climate-adaptation": {
        "topic": "Assess urban climate adaptation options",
        "objectives": ["Characterize climate hazard", "Compare adaptation options", "Assess equity effects"],
        "search_queries": [
            {"query": "urban climate hazard assessment", "purpose": "Define the local context"},
            {"query": "urban adaptation implementation case study", "purpose": "Identify practical options"},
            {"query": "climate adaptation equity policy guidelines", "purpose": "Find authoritative equity guidance"},
        ],
        "report_outline": ["Climate hazard context", "Adaptation options", "Equity effects"],
    },
    "semiconductor-supply-chain": P9_BASELINE_PLAN_PAYLOADS["semiconductor-supply-chain"],
    "clinical-evidence-translation": {
        "topic": "Translate clinical evidence into practice",
        "objectives": ["Assess clinical evidence", "Map care workflow", "Measure patient outcome"],
        "search_queries": [
            {"query": "clinical evidence guideline translation", "purpose": "Locate authoritative evidence"},
            {"query": "clinical workflow implementation", "purpose": "Map the care process"},
            {"query": "clinical outcome implementation case study", "purpose": "Find practice examples"},
        ],
        "report_outline": ["Clinical evidence", "Care workflow", "Patient outcome"],
    },
    "enterprise-ai-evaluation": {
        "topic": "Evaluate enterprise AI systems",
        "objectives": ["Benchmark AI capability", "Assess safety controls", "Plan procurement criteria"],
        "search_queries": [
            {"query": "enterprise AI capability benchmark comparison", "purpose": "Compare capabilities"},
            {"query": "enterprise AI safety risk standards", "purpose": "Review control expectations"},
            {"query": "enterprise AI procurement implementation", "purpose": "Identify buying criteria"},
        ],
        "report_outline": ["Capability benchmark", "Safety controls", "Procurement criteria"],
    },
    "renewable-grid-integration": {
        "topic": "Plan renewable grid integration",
        "objectives": ["Assess renewable generation", "Model grid integration", "Plan storage needs"],
        "search_queries": [
            {"query": "renewable generation grid mechanism", "purpose": "Explain system behavior"},
            {"query": "renewable grid storage comparison", "purpose": "Compare storage choices"},
            {"query": "renewable grid implementation challenges", "purpose": "Identify delivery constraints"},
        ],
        "report_outline": ["Renewable generation", "Grid integration", "Storage needs"],
    },
}
