import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'patient_dossiers')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
MONGO_URI = 'mongodb://localhost:27017/'
DATABASE_NAME = 'medical_dossier_db'
SECRET_KEY = 'super_secret_key_for_session_management'
