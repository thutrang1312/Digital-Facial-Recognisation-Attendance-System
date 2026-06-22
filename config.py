import os

DB_NAME = "attendance_db"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(APP_DIR, "dataset")
STATUS_FILE = os.path.join(APP_DIR, "train_status.json")