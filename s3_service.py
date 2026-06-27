import boto3
from flask import current_app
import os

def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=current_app.config['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=current_app.config['AWS_SECRET_ACCESS_KEY'],
        region_name=current_app.config['AWS_REGION']
    )

def upload_file_to_s3(local_filepath, s3_key):
    """Uploads a file to S3 and returns its public URL."""
    s3 = get_s3_client()
    bucket = current_app.config['AWS_BUCKET_NAME']
    
    s3.upload_file(
        local_filepath, 
        bucket, 
        s3_key,
        ExtraArgs={'ACL': 'public-read'} # Ensures streaming directly via public URL
    )
    
    return f"https://{bucket}.s3.{current_app.config['AWS_REGION']}.amazonaws.com/{s3_key}"

def delete_file_from_s3(s3_key):
    """Removes media assets from S3 bucket."""
    if not s3_key:
        return
    try:
        s3 = get_s3_client()
        s3.delete_object(Bucket=current_app.config['AWS_BUCKET_NAME'], Key=s3_key)
    except Exception as e:
        print(f"Error deleting {s3_key} from S3: {e}")