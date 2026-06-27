import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-12345')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'platform.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Absolute paths pointing directly to the static subdirectories
    UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
    THUMBNAIL_FOLDER = os.path.join(basedir, 'static', 'thumbnails')
    
    # Added mkv to match front-end specifications
    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'} 
    
    # Increased payload limit to 2GB (2 * 1024 * 1024 * 1024)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024