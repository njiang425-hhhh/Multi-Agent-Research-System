"""Fixed P5.1 offline cases, including success, partial, and failure scenarios."""

from src.evaluation.contracts import (
    EvaluationCase,
    EvaluationDataset,
    RegressionThresholds,
    ResearchQualityRubric,
)


FIXED_EVALUATION_DATASET = EvaluationDataset(
    dataset_id="researchos_offline_baseline",
    version="2",
    regression_thresholds=RegressionThresholds(
        min_expected_outcome_match_rate=1.0,
        min_quality_metric_pass_rate=1.0,
    ),
    cases=[
        EvaluationCase(
            case_id="ai-regulation-overview",
            query="Compare the current approaches to AI regulation in the EU, United States, and China.",
            description="Cross-jurisdiction policy research with multiple primary-source needs.",
            tags=["policy", "comparison"],
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=2,
                min_grounded_citations=2,
                min_report_sections=2,
                min_report_characters=120,
            ),
        ),
        EvaluationCase(
            case_id="urban-climate-adaptation",
            query="What climate-adaptation measures are most effective for dense coastal cities?",
            description="Evidence-oriented applied research with technical and public-sector sources.",
            tags=["climate", "evidence"],
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=2,
                min_grounded_citations=2,
                min_report_sections=2,
                min_report_characters=120,
            ),
        ),
        EvaluationCase(
            case_id="semiconductor-supply-chain",
            query="Assess key resilience risks in the global semiconductor supply chain.",
            description="Multi-factor industry research requiring synthesis across sources.",
            tags=["industry", "risk"],
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=2,
                min_grounded_citations=2,
                min_report_sections=2,
                min_report_characters=120,
            ),
        ),
        EvaluationCase(
            case_id="clinical-evidence-translation",
            query="What evidence and implementation constraints matter when translating a clinical intervention into routine care?",
            description="Health evidence synthesis requiring source provenance and a structured report.",
            tags=["health", "evidence", "implementation"],
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=2,
                min_grounded_citations=2,
                min_report_sections=2,
                min_report_characters=120,
            ),
        ),
        EvaluationCase(
            case_id="coastal-adaptation-partial-evidence",
            query="Summarize near-term coastal adaptation options when one planned evidence source is unavailable.",
            description="Intentional partial-evidence case: the report may be smaller but must cite validated available evidence.",
            tags=["climate", "partial", "evidence"],
            scenario="partial",
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=1,
                min_grounded_citations=1,
                min_report_sections=1,
                min_report_characters=80,
            ),
        ),
        EvaluationCase(
            case_id="search-provider-failure",
            query="Assess a research question when the injected search provider fails before returning any source.",
            description="Intentional failure case that verifies deterministic reporting of a terminal research failure.",
            tags=["failure", "provider", "search"],
            scenario="failure",
            expected_outcome="failed",
            quality_rubric=ResearchQualityRubric(
                min_distinct_sources=0,
                min_grounded_citations=0,
                min_report_sections=0,
                min_report_characters=0,
                require_top_level_heading=False,
            ),
        ),
    ],
)
