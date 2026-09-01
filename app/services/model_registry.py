"""Loading the served model together with its provenance.

The artifact is a pickle. Loading one under a different scikit-learn version
than it was written with does not degrade gracefully - it raises, often deep
inside a transformer, which is why the version it was trained with is recorded
and checked at startup rather than discovered in production.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import sklearn

logger = logging.getLogger(__name__)


@dataclass
class ModelBundle:
    pipeline: Any
    metadata: dict = field(default_factory=dict)

    @property
    def version(self) -> int | None:
        return self.metadata.get('version')

    @property
    def training_distribution(self) -> dict:
        return self.metadata.get('training_distribution', {})

    def summary(self) -> dict:
        """Everything about the model that is safe to expose publicly.

        Deliberately omits training_distribution, which is bulky and closer to
        the training data than an API consumer needs.
        """
        data = self.metadata.get('data', {})
        return {
            'version': self.metadata.get('version'),
            'trained_at': self.metadata.get('trained_at'),
            'model_type': self.metadata.get('model_type'),
            'hyperparameters': self.metadata.get('hyperparameters', {}),
            'sklearn_version': self.metadata.get('sklearn_version'),
            'sklearn_version_runtime': sklearn.__version__,
            'training_rows': data.get('rows_train'),
            'test_rows': data.get('rows_test'),
            'data_sha256': data.get('sha256'),
            'features': self.metadata.get('features', {}),
            'metrics': self.metadata.get('metrics', {}),
            'baseline_metrics': self.metadata.get('baseline_metrics', {}),
        }


def load_bundle(model_path: str, metadata_path: str) -> ModelBundle:
    pipeline = joblib.load(model_path)

    metadata: dict = {}
    path = Path(metadata_path)
    if path.exists():
        try:
            metadata = json.loads(path.read_text())
        except json.JSONDecodeError:
            logger.exception('Model metadata at %s is not valid JSON', metadata_path)
    else:
        logger.warning(
            'No model metadata at %s - version, metrics and drift detection are unavailable. '
            'Run: python -m training.train_model',
            metadata_path,
        )

    trained_with = metadata.get('sklearn_version')
    if trained_with and trained_with != sklearn.__version__:
        logger.warning(
            'Model v%s was pickled with scikit-learn %s but %s is installed. '
            'Predictions may differ or unpickling may fail.',
            metadata.get('version'),
            trained_with,
            sklearn.__version__,
        )

    return ModelBundle(pipeline=pipeline, metadata=metadata)
