import hashlib
import os

DATA_DIR = 'data'
DATA_FILE_NAME = 'car-details.csv'
DATA_FILE_PATH = os.path.join(DATA_DIR, DATA_FILE_NAME)

APP_DIR = 'app'
MODEL_DIR_NAME = 'models'
MODEL_NAME = 'model.joblib'
METADATA_NAME = 'model_metadata.json'
MODEL_DIR = os.path.join(APP_DIR, MODEL_DIR_NAME)
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_NAME)
METADATA_PATH = os.path.join(MODEL_DIR, METADATA_NAME)

TARGET = 'selling_price'
DROPPED_COLUMNS = ['name', 'model', 'edition']

# Deciles. Coarse enough that a normal sample does not look like drift, fine
# enough that a real shift shows up.
PSI_BINS = 10


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()
