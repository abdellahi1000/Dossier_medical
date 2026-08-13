import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'patient_dossiers')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'dcm'}

# In production, set MONGODB_URI to a real MongoDB Atlas connection string via
# an environment variable — "localhost" only ever works when MongoDB is
# running on the same machine as the app (e.g. local development).
MONGO_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
DATABASE_NAME = os.environ.get('DATABASE_NAME', 'Dossier_medical')
SECRET_KEY = os.environ.get('SECRET_KEY', 'super_secret_key_for_session_management')

# CNAM insurance coverage ratio applied to reservations paid via CNAM.
CNAM_COVERAGE_RATE = 0.9
