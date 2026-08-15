"""The small, fixed P3.2b offline evaluation dataset."""

from src.evaluation.contracts import EvaluationCase, EvaluationDataset


FIXED_EVALUATION_DATASET = EvaluationDataset(
    dataset_id="researchos_offline_baseline",
    version="1",
    cases=[
        EvaluationCase(
            case_id="ai-regulation-overview",
            query="Compare the current approaches to AI regulation in the EU, United States, and China.",
            description="Cross-jurisdiction policy research with multiple primary-source needs.",
            tags=["policy", "comparison"],
        ),
        EvaluationCase(
            case_id="urban-climate-adaptation",
            query="What climate-adaptation measures are most effective for dense coastal cities?",
            description="Evidence-oriented applied research with technical and public-sector sources.",
            tags=["climate", "evidence"],
        ),
        EvaluationCase(
            case_id="semiconductor-supply-chain",
            query="Assess key resilience risks in the global semiconductor supply chain.",
            description="Multi-factor industry research requiring synthesis across sources.",
            tags=["industry", "risk"],
        ),
    ],
)
