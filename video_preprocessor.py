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

def get_real_creation_time(file_path):
    """
    Use ffprobe to get the creation_time from the file's metadata.
    If not found, fall back to os.path.getctime.
    Returns a datetime object.
    """
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        # Try to find creation_time in streams or format tags
        creation_str = None

        # 1) Check 'streams' array
        if 'streams' in data:
            for stream in data['streams']:
                if 'tags' in stream and 'creation_time' in stream['tags']:
                    creation_str = stream['tags']['creation_time']
                    break

        # 2) If still None, check 'format' section
        if not creation_str and 'format' in data and 'tags' in data['format']:
            if 'creation_time' in data['format']['tags']:
                creation_str = data['format']['tags']['creation_time']

        if creation_str:
            # creation_str is typically ISO8601, e.g. "2025-01-28T12:34:56.000000Z"
            # Strip the trailing 'Z' and parse as UTC if needed
            return datetime.fromisoformat(creation_str.replace('Z','+00:00'))
        else:
            # Fallback: filesystem ctime
            return datetime.fromtimestamp(os.path.getctime(file_path))

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError):
        # If ffprobe fails or the JSON is malformed, fallback to filesystem ctime
        return datetime.fromtimestamp(os.path.getctime(file_path))

def copy_files(source_dir, backup_subdir):
    """
    Copies new .mp4 files from the source_dir to the backup_subdir,
    capturing their real creation time (via ffprobe).
    Returns a list of (dest_path, creation_dt).
    """
    processed_files = set()
    if os.path.exists(PROCESSED_FILES):
        with open(PROCESSED_FILES, 'r') as pf:
            processed_files = set(pf.read().splitlines())
    
    file_data = []
    processed_srcs = []

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(".mp4"):
                src = os.path.join(root, file)
                if src not in processed_files:
                    dest_dir = os.path.join(backup_subdir, os.path.relpath(root, source_dir))
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, file)

                    # Copy the file
                    shutil.copy2(src, dest)

                    # Get the real creation datetime
                    creation_dt = get_real_creation_time(src)
                    
                    file_data.append((dest, creation_dt))
                    processed_srcs.append(src)

    # Mark these files as processed so we don't copy them again
    if processed_srcs:
        with open(PROCESSED_FILES, 'a') as pf:
            pf.write('\n'.join(processed_srcs) + '\n')

    return file_data  # [(dest_path, datetime), ...]

def escape_drawtext_text(text):
    """
    Escape special characters that confuse the drawtext filter:
      - backslash
      - single quote
      - colon
    """
    # First escape backslashes, then single quotes, then colons
    text = text.replace('\\', '\\\\')   # \ -> \\
    text = text.replace("'", "\\'")     # ' -> \'
    text = text.replace(':', '\\:')     # : -> \:
    return text

def preprocess_video(item, tmp_dir):
    """
    Overlays the date/time onto each video using drawtext.
    """
    input_file, creation_dt = item
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")

    # Convert datetime to a more readable string, e.g. "2025-01-28 12:34:56"
    dt_str = creation_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Build the text we want to display
    text_to_display = f"Recorded: {dt_str}"

    # Escape for drawtext
    escaped_text = escape_drawtext_text(text_to_display)

    # Build our -vf filter string carefully
    drawtext_filter = (
        f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:"
        f"text='{escaped_text}':"
        "x=100:y=100:"
        "fontcolor=white:fontsize=24:"
        "box=1:boxcolor=black@0.5"
    )

    command = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-vf", drawtext_filter,
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac",
        output_file
    ]

    print(f"Preprocessing: {input_file}")
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Success: {output_file} created.")
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"Error preprocessing {input_file}:\n{e.stderr}")
        return None

def create_file_list(tmp_dir, file_data):
    """
    Create a file_list.txt sorted by the real creation datetime.
    Only include files that successfully preprocessed.
    """
    file_list_path = os.path.join(tmp_dir, "file_list.txt")

    # Build a list of (preprocessed_path, creation_dt)
    processed_files = []
    for dest_path, creation_dt in file_data:
        pre_file = os.path.join(tmp_dir, f"pre_{Path(dest_path).name}")
        if os.path.exists(pre_file):
            processed_files.append((pre_file, creation_dt))

    # Sort by creation_dt
    processed_files.sort(key=lambda x: x[1])

    print("\nFinal order of preprocessed files by creation time:")
    for path, dt in processed_files:
        print(f"{Path(path).name} - {dt}")

    # Write to file_list.txt
    with open(file_list_path, 'w') as f:
        for video, _ in processed_files:
            f.write(f"file '{video}'\n")
    return file_list_path

def concatenate_videos(file_list_path, output_file):
    command = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c", "copy",  # Stream copy for faster concatenation
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
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(lambda item: preprocess_video(item, tmp_dir), file_data))

    preprocessed_files = [r for r in results if r]
    if not preprocessed_files:
        print("No files preprocessed successfully.")
        return

    print("\nCreating file list in chronological order...")
    file_list = create_file_list(tmp_dir, file_data)
    
    print("\nConcatenating videos...")
    output_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, output_file)

    print("\nCleaning up temporary files...")
    shutil.rmtree(tmp_dir)

    elapsed = datetime.now() - start_time
    print(f"Total processing time: {elapsed}")

if __name__ == "__main__":
    main()
