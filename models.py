from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    
    # Relationship
    videos = db.relationship('Video', backref='author', lazy=True, cascade="all, delete-orphan")

class Video(db.Model):
    __tablename__ = 'videos'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    video_url = db.Column(db.String(500), nullable=True)  # Populated after S3 upload
    thumbnail_url = db.Column(db.String(500), nullable=True)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    views = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='Processing')  # Processing, Ready, Failed
    
    # 💡 ADDED: Tracks the real-time transcoding percentage (0 to 100)
    encoding_progress = db.Column(db.Integer, default=0)
    
    # Local file tracking keys for cleaning up temp processing files
    s3_video_key = db.Column(db.String(255), nullable=True)
    s3_thumbnail_key = db.Column(db.String(255), nullable=True)