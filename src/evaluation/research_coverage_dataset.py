"""Versioned fake fixtures for the P10.1 research coverage baseline."""

from __future__ import annotations

from typing import Any

from src.evaluation.research_coverage import (
    ResearchCoverageCase,
    ResearchCoverageDataset,
)


RESEARCH_COVERAGE_DATASET = ResearchCoverageDataset(
    dataset_id="researchos_research_coverage",
    version="p10.1.v1",
    cases=[
        ResearchCoverageCase(
            case_id="ai-regulation-jurisdiction-balance",
            query="Compare AI regulation coverage across the EU, US, and China.",
            required_facets=["eu", "us", "china", "risk", "implementation"],
        ),
        ResearchCoverageCase(
            case_id="clinical-evidence-practice-translation",
            query="Translate clinical evidence into routine practice.",
            required_facets=["evidence", "workflow", "outcome"],
        ),
        ResearchCoverageCase(
            case_id="enterprise-ai-evaluation-coverage",
            query="Evaluate enterprise AI systems across capability, safety, and procurement.",
            required_facets=["capability", "safety", "procurement"],
        ),
    ],
)


P10_RESEARCH_COVERAGE_PLAN_PAYLOADS: dict[str, dict[str, Any]] = {
    "ai-regulation-jurisdiction-balance": {
        "topic": "Compare AI regulation coverage across the EU, US, and China",
        "objectives": [
            "Compare EU AI regulation and risk classifications",
            "Compare US AI regulation implementation guidance",
            "Compare China AI regulation implementation requirements",
        ],
        "search_queries": [
            {
                "query": "eu ai regulation risk implementation",
                "purpose": "comparison: Cover EU risk and implementation requirements",
            },
            {
                "query": "us ai regulation risk implementation",
                "purpose": "comparison: Cover US risk and implementation requirements",
            },
            {
                "query": "china ai regulation risk implementation",
                "purpose": "comparison: Cover China risk and implementation requirements",
            },
        ],
        "report_outline": [
            "EU regulation and risk classification",
            "US regulation and implementation guidance",
            "China regulation and implementation requirements",
        ],
    },
    "clinical-evidence-practice-translation": {
        "topic": "Translate clinical evidence into routine practice",
        "objectives": [
            "Assess clinical evidence quality",
            "Map practice workflow constraints",
            "Measure patient outcome implications",
        ],
        "search_queries": [
            {
                "query": "clinical evidence guideline translation",
                "purpose": "authority: Cover evidence sources for clinical translation",
            },
            {
                "query": "clinical workflow implementation translation",
                "purpose": "implementation: Cover workflow constraints in practice",
            },
            {
                "query": "clinical outcome practice translation",
                "purpose": "implementation: Cover outcome measurement after translation",
            },
        ],
        "report_outline": [
            "Clinical evidence base",
            "Care workflow constraints",
            "Outcome measurement",
        ],
    },
    "enterprise-ai-evaluation-coverage": {
        "topic": "Evaluate enterprise AI systems",
        "objectives": [
            "Benchmark capability coverage",
            "Assess safety coverage",
            "Define procurement coverage",
        ],
        "search_queries": [
            {
                "query": "enterprise ai capability benchmark",
                "purpose": "comparison: Cover capability benchmark criteria",
            },
            {
                "query": "enterprise ai safety risk standard",
                "purpose": "risk_limitations: Cover safety risk controls",
            },
            {
                "query": "enterprise ai procurement evaluation",
                "purpose": "implementation: Cover procurement decision criteria",
            },
        ],
        "report_outline": [
            "Capability benchmarks",
            "Safety controls",
            "Procurement criteria",
        ],
    },
}


P10_RESEARCH_COVERAGE_SEARCH_PAYLOADS: dict[str, dict[str, list[dict[str, str]]]] = {
    "ai-regulation-jurisdiction-balance": {
        "eu ai regulation risk implementation": [
            {
                "title": "EU AI Act risk implementation overview",
                "url": "https://eu.example.org/ai-risk-implementation",
                "snippet": "EU risk implementation requirements for AI systems.",
            },
            {
                "title": "EU AI risk categories",
                "url": "https://eu-policy.example.org/risk-categories",
                "snippet": "EU risk categories and compliance obligations.",
            },
            {
                "title": "EU implementation timeline",
                "url": "https://eu-timeline.example.org/implementation",
                "snippet": "EU implementation timeline for AI regulation.",
            },
        ],
        "us ai regulation risk implementation": [
            {
                "title": "US AI risk management implementation",
                "url": "https://us.example.org/ai-risk-management",
                "snippet": "US risk management and implementation guidance.",
            },
            {
                "title": "US agency AI implementation rules",
                "url": "https://us-agency.example.org/ai-implementation",
                "snippet": "US agency implementation rules for AI systems.",
            },
            {
                "title": "US AI safety risk framework",
                "url": "https://us-framework.example.org/ai-risk",
                "snippet": "US safety risk framework for enterprise AI.",
            },
        ],
        "china ai regulation risk implementation": [
            {
                "title": "China AI regulation implementation",
                "url": "https://china.example.org/ai-implementation",
                "snippet": "China implementation obligations and risk controls.",
            },
            {
                "title": "China generative AI risk rules",
                "url": "https://china-rules.example.org/generative-ai-risk",
                "snippet": "China risk rules for generative AI services.",
            },
            {
                "title": "China algorithm regulation implementation",
                "url": "https://china-algorithm.example.org/implementation",
                "snippet": "China algorithm implementation requirements.",
            },
        ],
    },
    "clinical-evidence-practice-translation": {
        "clinical evidence guideline translation": [
            {
                "title": "Clinical evidence guideline translation",
                "url": "https://clinical-evidence.example.org/guideline",
                "snippet": "Evidence translation from guideline to practice.",
            },
            {
                "title": "Clinical evidence appraisal",
                "url": "https://evidence-appraisal.example.org/clinical",
                "snippet": "Evidence appraisal for clinical decisions.",
            },
            {
                "title": "Clinical evidence synthesis",
                "url": "https://synthesis.example.org/clinical-evidence",
                "snippet": "Evidence synthesis methods for practice translation.",
            },
        ],
        "clinical workflow implementation translation": [
            {
                "title": "Clinical workflow implementation",
                "url": "https://workflow.example.org/clinical-implementation",
                "snippet": "Workflow implementation barriers in routine care.",
            },
            {
                "title": "Care workflow translation",
                "url": "https://care-workflow.example.org/translation",
                "snippet": "Workflow translation for clinical teams.",
            },
            {
                "title": "Clinical pathway workflow",
                "url": "https://pathway.example.org/workflow",
                "snippet": "Workflow redesign for clinical pathways.",
            },
        ],
        "clinical outcome practice translation": [
            {
                "title": "Clinical outcome measurement",
                "url": "https://outcome.example.org/clinical-measurement",
                "snippet": "Outcome measurement after practice translation.",
            },
            {
                "title": "Patient outcome translation",
                "url": "https://patient-outcome.example.org/translation",
                "snippet": "Outcome tracking for clinical implementation.",
            },
            {
                "title": "Routine care outcome evaluation",
                "url": "https://routine-care.example.org/outcome",
                "snippet": "Outcome evaluation in routine care.",
            },
        ],
    },
    "enterprise-ai-evaluation-coverage": {
        "enterprise ai capability benchmark": [
            {
                "title": "Enterprise AI capability benchmark",
                "url": "https://capability.example.org/benchmark",
                "snippet": "Capability benchmark criteria for enterprise AI systems.",
            },
            {
                "title": "AI capability evaluation",
                "url": "https://ai-capability.example.org/evaluation",
                "snippet": "Capability evaluation and benchmark methods.",
            },
            {
                "title": "Enterprise model capability scorecard",
                "url": "https://scorecard.example.org/capability",
                "snippet": "Capability scorecard for enterprise deployments.",
            },
        ],
        "enterprise ai safety risk standard": [
            {
                "title": "Enterprise AI safety standard",
                "url": "https://safety.example.org/standard",
                "snippet": "Safety risk standard for enterprise AI systems.",
            },
            {
                "title": "AI safety control framework",
                "url": "https://controls.example.org/safety",
                "snippet": "Safety controls for enterprise AI risk management.",
            },
            {
                "title": "Enterprise AI risk safety review",
                "url": "https://risk-review.example.org/safety",
                "snippet": "Safety risk review process for enterprise AI.",
            },
        ],
        "enterprise ai procurement evaluation": [
            {
                "title": "Enterprise AI procurement evaluation",
                "url": "https://procurement.example.org/evaluation",
                "snippet": "Procurement criteria for enterprise AI vendors.",
            },
            {
                "title": "AI vendor procurement checklist",
                "url": "https://vendor-checklist.example.org/procurement",
                "snippet": "Procurement checklist for enterprise AI evaluation.",
            },
            {
                "title": "Enterprise procurement governance",
                "url": "https://governance.example.org/procurement",
                "snippet": "Procurement governance for enterprise AI systems.",
            },
        ],
    },
}


P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS: dict[str, str] = {
    url: f"Full extracted content for {item['title']}. {item['snippet']}"
    for case_payload in P10_RESEARCH_COVERAGE_SEARCH_PAYLOADS.values()
    for query_payload in case_payload.values()
    for item in query_payload
    for url in [item["url"]]
}


P10_RESEARCH_COVERAGE_FAKE_PAYLOADS: dict[str, Any] = {
    "planner": P10_RESEARCH_COVERAGE_PLAN_PAYLOADS,
    "search": P10_RESEARCH_COVERAGE_SEARCH_PAYLOADS,
    "extract": P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS,
}
