import os
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Configuration
SOURCE_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups/_FakeSD"
BACKUP_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups"
COMPILE_DIR = os.path.join(BACKUP_DIR, "Compilations")
PROCESSED_FILES = os.path.join(BACKUP_DIR, ".processed_files.txt")
MAX_WORKERS = 4

def prompt_for_project_name():
    return input("Enter project name (e.g., November 2024): ").strip()

def initialize_directories(project_name):
    backup_subdir = os.path.join(BACKUP_DIR, project_name)
    tmp_dir = os.path.join(backup_subdir, "tmp")
    os.makedirs(COMPILE_DIR, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    return backup_subdir, tmp_dir

def get_real_creation_time(video_path):
    """
    Use ffprobe to fetch the original creation_time from video metadata.
    If not found, fall back to the file's OS ctime.
    Returns a float representing the UNIX timestamp (seconds since epoch).
    """
    command = [
        "ffprobe", 
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]
    
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # Attempt to find creation_time in format tags
        creation_str = None
        
        if "format" in data and "tags" in data["format"]:
            creation_str = data["format"]["tags"].get("creation_time")
        
        # If not in format, attempt to find in streams
        if not creation_str and "streams" in data:
            for stream in data["streams"]:
                if "tags" in stream and "creation_time" in stream["tags"]:
                    creation_str = stream["tags"]["creation_time"]
                    break
        
        if creation_str:
            # Attempt to parse the creation_time string as an ISO8601 date
            # e.g. "2022-07-19T14:30:00.000000Z"
            dt = datetime.fromisoformat(creation_str.replace("Z", "+00:00"))
            return dt.timestamp()
        else:
            # Fallback: use file system ctime if no metadata creation_time found
            return os.path.getctime(video_path)
    
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        # On any error, also fallback
        print(f"ffprobe failed for {video_path} - {e}, falling back to os.path.getctime().")
        return os.path.getctime(video_path)

def copy_files(source_dir, backup_subdir):
    processed_files = set()
    if os.path.exists(PROCESSED_FILES):
        with open(PROCESSED_FILES, 'r') as pf:
            processed_files = set(pf.read().splitlines())
    
    file_data = []  # List of tuples: (dest_path, real_creation_time)
    processed_srcs = []

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(".mp4"):
                src = os.path.join(root, file)
                if src not in processed_files:
                    # Get real creation time via ffprobe BEFORE copying
                    creation_time = get_real_creation_time(src)
                    
                    dest_dir = os.path.join(backup_subdir, os.path.relpath(root, source_dir))
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, file)
                    shutil.copy2(src, dest)
                    
                    file_data.append((dest, creation_time))
                    processed_srcs.append(src)

    # Update .processed_files.txt
    if processed_srcs:
        with open(PROCESSED_FILES, 'a') as pf:
            pf.write('\n'.join(processed_srcs) + '\n')

    return file_data  # Return list of (dest_path, real_creation_time)

def preprocess_video(input_file, tmp_dir):
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")
    static_text = "sample text"

    command = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-vf", f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{static_text}':x=100:y=100:fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", output_file
    ]

    print(f"Processing: {input_file}")
    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Success: {output_file} created.")
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"Error processing {input_file}:\n{e.stderr}")
        return None

def create_file_list(tmp_dir, file_data):
    """Create file list sorted by the real creation times."""
    file_list_path = os.path.join(tmp_dir, "file_list.txt")
    
    # Map preprocessed files to real creation times
    processed_files = []
    for dest_path, creation_time in file_data:
        preprocessed = os.path.join(tmp_dir, f"pre_{Path(dest_path).name}")
        if os.path.exists(preprocessed):
            processed_files.append((preprocessed, creation_time))
    
    # Sort by our real creation time
    processed_files.sort(key=lambda x: x[1])
    
    print("\nSorted files by real creation time (from metadata if available):")
    for path, time in processed_files:
        print(f"{Path(path).name} - {datetime.fromtimestamp(time)}")
    
    # Write to file list
    with open(file_list_path, 'w') as f:
        for video, _ in processed_files:
            f.write(f"file '{video}'\n")
            
    return file_list_path

def concatenate_videos(file_list_path, output_file):
    command = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c", "copy",  # Use stream copy for faster concatenation
        output_file
    ]
    try:
        subprocess.run(command, check=True)
        print(f"Concatenation complete: {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Concatenation failed:\n{e.stderr}")

def main():
    start_time = datetime.now()
    project_name = prompt_for_project_name()
    backup_subdir, tmp_dir = initialize_directories(project_name)

    print("Copying files...")
    file_data = copy_files(SOURCE_DIR, backup_subdir)
    if not file_data:
        print("No new files to process.")
        return

    print("\nPreprocessing videos...")
    input_files = [dest for dest, _ in file_data]
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(lambda f: preprocess_video(f, tmp_dir), input_files))
    
    preprocessed_files = [f for f in results if f]
    if not preprocessed_files:
        print("No files preprocessed successfully.")
        return

    print("\nCreating file list...")
    file_list = create_file_list(tmp_dir, file_data)
    
    print("\nConcatenating videos...")
    output_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, output_file)

    print("\nCleaning up...")
    shutil.rmtree(tmp_dir)
    print(f"Total processing time: {datetime.now() - start_time}")

if __name__ == "__main__":
    main()
