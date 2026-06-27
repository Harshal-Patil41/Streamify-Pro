import os
import re
import subprocess
from models import db, Video
# 💡 Import the S3 cleanup tool to ensure old temp records drop off gracefully
from services.s3_service import delete_file_from_s3

def get_video_duration(input_video_path):
    """Helper to extract total duration of the video using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', input_video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Duration Extraction Error: {str(e)}")
        return 0.0

def process_video_pipeline(app, video_id, temp_raw_path):
    with app.app_context():
        video = Video.query.get(video_id)
        if not video:
            return

        try:
            # 1. Fetch total duration for tracking percentage calculations
            total_duration = get_video_duration(temp_raw_path)
            
            # 2. Build dedicated local directories for HLS segments split tracking
            hls_output_dir = os.path.join('static', 'streams', f"video_{video_id}")
            os.makedirs(hls_output_dir, exist_ok=True)
            
            for variant in ['v0', 'v1', 'v2']:
                os.makedirs(os.path.join(hls_output_dir, variant), exist_ok=True)

            # 3. Standard HLS generation command matrix mapping (1080p, 720p, 480p targets)
            cmd = [
                'ffmpeg', '-y', '-i', temp_raw_path,
                '-preset', 'fast', '-g', '48', '-sc_threshold', '0',
                '-map', '0:v:0', '-map', '0:a:0',
                '-map', '0:v:0', '-map', '0:a:0',
                '-map', '0:v:0', '-map', '0:a:0',
                '-c:v:0', 'libx264', '-b:v:0', '4000k', '-maxrate:v:0', '4300k', '-bufsize:v:0', '6000k', '-s:v:0', '1920x1080',
                '-c:v:1', 'libx264', '-b:v:1', '2500k', '-maxrate:v:1', '2700k', '-bufsize:v:1', '3500k', '-s:v:1', '1280x720',
                '-c:v:2', 'libx264', '-b:v:2', '1000k', '-maxrate:v:2', '1100k', '-bufsize:v:2', '1500k', '-s:v:2', '854x480',
                '-c:a', 'aac', '-b:a', '128k',
                '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'vod',
                '-hls_segment_filename', os.path.join(hls_output_dir, 'v%v/file_%03d.ts'),
                '-master_pl_name', 'master.m3u8',
                os.path.join(hls_output_dir, 'v%v/manifest.m3u8')
            ]

            # 4. Start process sub-shell and pipe log stream strings natively
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, text=True)
            
            # Regular expression designed to pull raw timestamp variables out of FFmpeg terminal output
            time_regex = re.compile(r"time=(\d+):(\d+):(\d+.\d+)")

            while True:
                line = process.stdout.readline()
                if not line:
                    break
                
                # Check for FFmpeg timestamp progress match: time=00:00:05.42
                match = time_regex.search(line)
                if match and total_duration > 0:
                    hours, minutes, seconds = match.groups()
                    current_time = (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
                    
                    # Calculate current execution percentage safely limited to 99% until complete
                    percent = min(int((current_time / total_duration) * 100), 99)
                    
                    # Update database column properties live
                    video.encoding_progress = percent
                    db.session.commit()

            process.wait()

            # 5. Check if process concluded with clean exits
            if process.returncode == 0:
                video.video_url = f"/static/streams/video_{video_id}/master.m3u8"
                video.status = 'Ready'
                video.encoding_progress = 100
            else:
                video.status = 'Error'
                
            db.session.commit()

            # 6. Housekeeping: Remove heavy temporary local raw video file to save server disk storage space
            if os.path.exists(temp_raw_path):
                os.remove(temp_raw_path)

        except Exception as e:
            print(f"Pipeline Transcoding Execution Failed: {str(e)}")
            video.status = 'Error'
            db.session.commit()