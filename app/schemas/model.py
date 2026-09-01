from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    version: int | None
    trained_at: str | None
    model_type: str | None
    hyperparameters: dict = {}
    sklearn_version: str | None
    sklearn_version_runtime: str
    version_match: bool
    training_rows: int | None
    test_rows: int | None
    data_sha256: str | None
    features: dict = {}
    metrics: dict = {}
    baseline_metrics: dict = {}


class FeatureDrift(BaseModel):
    feature: str
    psi: float
    status: str


class DriftReport(BaseModel):
    configured: bool
    ready: bool
    window_samples: int
    min_samples: int
    worst_psi: float
    status: str
    features: list[FeatureDrift]


class ActualPrice(BaseModel):
    actual_price: float = Field(gt=0, le=100_000_000, description='Real sale price')


class ScoredPrediction(BaseModel):
    id: int
    predicted_price: float
    actual_price: float
    absolute_error: float


class ModelPerformance(BaseModel):
    scored: int
    live_mae: float | None
    live_mape_pct: float | None
    training_mae: float | None
    training_mape_pct: float | None
    note: str
