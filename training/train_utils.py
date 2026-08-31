import os

DATA_DIR = 'data'
DATA_FILE_NAME = 'car-details.csv'
DATA_FILE_PATH = os.path.join(DATA_DIR, DATA_FILE_NAME)

APP_DIR = 'app'
MODEL_DIR_NAME = 'models'
MODEL_NAME = 'model.joblib'
MODEL_DIR = os.path.join(APP_DIR, MODEL_DIR_NAME)
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_NAME)