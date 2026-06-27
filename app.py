import os
import threading
import time
import tempfile
import subprocess
import cv2  # Pure Python frame extraction for thumbnails
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Import your existing database setup, configs, and background tasks
from models import db, User, Video
from config import Config
from services.ffmpeg_service import process_video_pipeline
from services.s3_service import delete_file_from_s3

app = Flask(__name__)

# ==========================================
# ⚙️ SYSTEM CONFIGURATIONS
# ==========================================
app.config.from_object(Config)

# Ensure local directories for temporary uploads, thumbnails, and local HLS streams exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['THUMBNAIL_FOLDER'], exist_ok=True)
os.makedirs(os.path.join('static', 'streams'), exist_ok=True)

db.init_app(app)

with app.app_context():
    db.create_all()

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==========================================
# 🌐 ROUTING LOGIC
# ==========================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if User.query.filter_by(email=email).first() or User.query.filter_by(username=username).first():
            flash('Username or Email already exists.', 'danger')
            return redirect(url_for('register'))
            
        hashed_pw = generate_password_hash(password, method='scrypt')
        new_user = User(username=username, email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    search_query = request.args.get('search', '')
    if search_query:
        videos = Video.query.filter(Video.title.contains(search_query)).all()
    else:
        videos = Video.query.all()
    return render_template('dashboard.html', videos=videos, search_query=search_query)

# ==========================================================
# ⚡ LIVE PROGRESS TRACKING ENDPOINT MATRIX
# ==========================================================
@app.route('/api/video-progress')
@login_required
def get_video_progress():
    processing_videos = Video.query.filter_by(user_id=current_user.id, status='Processing').all()
    data = {video.id: video.encoding_progress for video in processing_videos}
    return jsonify(data)

# ==========================================
# 📥 VIDEO UPLOAD ENGINE (INTEGRATED FOR HLS)
# ==========================================
@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        if 'video_file' not in request.files:
            flash('No video file slot discovered.', 'danger')
            return redirect(request.url)
            
        file = request.files['video_file']
        title = request.form.get('title')
        
        if file.filename == '' or not title:
            flash('Missing title or file.', 'danger')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            base_name = os.path.splitext(filename)[0]
            
            # 1. Save raw video asset temporarily for backend processing
            temp_raw_path = os.path.join(app.config['UPLOAD_FOLDER'], f"raw_{int(time.time())}_{filename}")
            file.save(temp_raw_path)
            
            # 2. Extract Thumbnail using OpenCV (Hardened to skip black frames)
            thumb_filename = f"thumb_{base_name}_{int(time.time())}.jpg"
            local_thumb_path = os.path.join('static', 'thumbnails', thumb_filename)
            saved_thumb_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=640&auto=format&fit=crop"
            
            try:
                cap = cv2.VideoCapture(temp_raw_path)
                cap.set(cv2.CAP_PROP_POS_FRAMES, 30)  # Skip to frame 30 to avoid black frame 0
                success, frame = cap.read()
                
                if not success:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    success, frame = cap.read()
                    
                if success:
                    cv2.imwrite(local_thumb_path, frame)
                    saved_thumb_url = f"/static/thumbnails/{thumb_filename}"
                cap.release()
            except Exception as e:
                print(f"❌ OpenCV Extraction Error: {str(e)}")

            # 3. Save placeholder into DB with 'Processing' state status
            new_video = Video(
                title=title, 
                user_id=current_user.id, 
                status='Processing',
                thumbnail_url=saved_thumb_url,
                video_url=""
            )
            db.session.add(new_video)
            db.session.commit()
            
            # 4. Offload HLS stream encoding splits into an asynchronous background thread
            threading.Thread(
                target=process_video_pipeline, 
                args=(app, new_video.id, temp_raw_path)
            ).start()
            
            flash('Video uploaded successfully! Dynamic ABR HLS processing started in the background.', 'info')
            return redirect(url_for('dashboard'))
            
        flash('Unsupported video format.', 'danger')
    return render_template('upload.html')

@app.route('/watch/<int:video_id>')
@login_required
def watch(video_id):
    video = Video.query.get_or_404(video_id)
    if video.status == 'Ready':
        video.views += 1
        db.session.commit()
    return render_template('watch.html', video=video)

@app.route('/download/<int:video_id>', methods=['GET'])
@login_required
def download_video(video_id):
    from flask import after_this_request
    video = Video.query.get_or_404(video_id)
    
    base_dir = os.path.abspath(os.path.dirname(__file__))
    hls_dir = os.path.join(base_dir, 'static', 'streams', f"video_{video_id}")
    master_playlist = os.path.join(hls_dir, "master.m3u8")
    
    if not os.path.exists(master_playlist):
        flash(f'Stream manifest configuration not found locally on disk.', 'danger')
        return redirect(url_for('watch', video_id=video_id))
    
    try:
        temp_dir = tempfile.gettempdir()
        download_filename = f"download_{video_id}_{int(time.time())}.mp4"
        export_path = os.path.join(temp_dir, download_filename)
        
        cmd = [
            'ffmpeg', '-y', 
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-allowed_extensions', 'ALL', 
            '-i', master_playlist, 
            '-c', 'copy', 
            export_path
        ]
        
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, shell=True if os.name == 'nt' else False)
        
        @after_this_request
        def remove_temporary_file(response):
            try:
                if os.path.exists(export_path):
                    os.remove(export_path)
            except Exception as e:
                app.logger.error(f"Error cleaning up staging download artifact: {e}")
            return response

        return send_file(export_path, as_attachment=True, download_name=f"{secure_filename(video.title)}.mp4", mimetype='video/mp4')
        
    except Exception as e:
        print(f"❌ System Exception: {str(e)}")
        flash(f'Compilation engine failure: {str(e)}', 'danger')
        return redirect(url_for('watch', video_id=video_id))

@app.route('/video/edit/<int:video_id>', methods=['POST'])
@login_required
def edit_video(video_id):
    video = Video.query.get_or_404(video_id)
    if video.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    new_title = request.form.get('title')
    if new_title:
        video.title = new_title
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/video/delete/<int:video_id>', methods=['POST'])
@login_required
def delete_video(video_id):
    video = Video.query.get_or_404(video_id)
    if hasattr(video, 's3_video_key') and video.s3_video_key:
        delete_file_from_s3(video.s3_video_key)
    if hasattr(video, 's3_thumbnail_key') and video.s3_thumbnail_key:
        delete_file_from_s3(video.s3_thumbnail_key)
        
    db.session.delete(video)
    db.session.commit()
    return redirect(url_for('dashboard'))

# 🟢 RE-ALIGNED TOP-LEVEL FUNCTION: Fixed indentation to cure BuildError
@app.route('/analytics')
@login_required
def analytics():
    all_videos = Video.query.all()
    total_videos = len(all_videos)
    total_views = sum(video.views for video in all_videos)
    
    # Fetches entire tracking histories without layout clipping limitations
    top_videos = Video.query.order_by(Video.views.desc()).all()
    recent_videos = Video.query.order_by(Video.id.desc()).all()
    
    return render_template('analytics.html', 
                           total_videos=total_videos, 
                           total_views=total_views, 
                           top_videos=top_videos, 
                           recent_videos=recent_videos)

# 🛑 TEMPORARY MASTER CLEANUP ENGINE
@app.route('/master-wipe-all-data')
@login_required  
def master_wipe_all_data():
    if current_user.email != 'harshalpatil2612005@gmail.com':
        return jsonify({'status': 'error', 'message': 'Unauthorized.'}), 403
    try:
        db.session.query(Video).delete()
        db.session.commit()
        return "⚡ SUCCESS: All records purged!"
    except Exception as e:
        db.session.rollback()
        return f"❌ ERROR: {str(e)}"      

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)