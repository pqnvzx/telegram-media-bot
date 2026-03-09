import os
import logging
import subprocess
from typing import Optional

from config import MAX_FILE_SIZE, TARGET_SIZE_MB

logger = logging.getLogger(__name__)

def get_audio_duration(file_path: str) -> float:
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
            capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Error getting audio duration: {e}")
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        estimated_duration = file_size_mb * 60 
        return estimated_duration

def compress_audio_file(input_file: str, target_size_mb: int = TARGET_SIZE_MB) -> Optional[str]:
    output_file = input_file.replace('.mp3', '_compressed.mp3')
    
    try:
        duration = get_audio_duration(input_file)
        target_bitrate_kbps = int((target_size_mb * 8192) / duration)
        target_bitrate_kbps = max(32, min(target_bitrate_kbps, 320))
        
        logger.info(f"Compressing {input_file} to {target_bitrate_kbps} kbps (target size: {target_size_mb} MB)")
    
        cmd = [
            'ffmpeg', '-i', input_file,
            '-b:a', f'{target_bitrate_kbps}k',
            '-map', '0:a',
            '-y',  
            output_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            return None
        
        if os.path.exists(output_file):
            compressed_size = os.path.getsize(output_file)
            logger.info(f"Successfully compressed to {compressed_size} bytes ({compressed_size/(1024*1024):.2f} MB)")
            return output_file
        else:
            logger.error("Compressed file not found")
            return None
            
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg compression timed out")
        return None
    except Exception as e:
        logger.error(f"Compression error: {e}")
        return None