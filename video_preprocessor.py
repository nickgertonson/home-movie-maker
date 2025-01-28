import os
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
TMP_DIR = None
MAX_WORKERS = 4  # Adjust based on system resources


def prompt_for_project_name():
    """Prompt user for the project name."""
    return input("Enter project name (e.g., November 2024): ").strip()


def initialize_directories(project_name):
    """Ensure necessary directories exist and return paths."""
    global TMP_DIR
    backup_subdir = os.path.join(BACKUP_DIR, project_name)
    TMP_DIR = os.path.join(backup_subdir, "tmp")
    os.makedirs(COMPILE_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    return backup_subdir, TMP_DIR


def copy_files(source_dir, backup_subdir):
    """Copy new MP4 files from source to backup."""
    processed_files = set(Path(PROCESSED_FILES).read_text().splitlines()) if os.path.exists(PROCESSED_FILES) else set()
    new_files = []

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(".mp4"):
                src = os.path.join(root, file)
                if src not in processed_files:
                    dest_dir = os.path.join(backup_subdir, os.path.relpath(root, source_dir))
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, file)
                    shutil.copy2(src, dest)
                    new_files.append(src)

    with open(PROCESSED_FILES, "a") as pf:
        pf.write("\n".join(new_files) + "\n")

    return new_files


def get_mp4_files(directory):
    """Recursively find all MP4 files in a directory."""
    mp4_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(".mp4"):
                mp4_files.append(os.path.join(root, file))
    return mp4_files


def preprocess_video(input_file, tmp_dir):
    """Preprocess video using ffmpeg."""
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")
    static_text = "sample text"

    command = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-vf", f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='{static_text}':"
               f"x=100:y=100:fontcolor=white:fontsize=24:box=1:boxcolor=black@0",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        output_file,
    ]

    print(f"Processing: {input_file}")
    print("Command:", " ".join(command))

    try:
        subprocess.run(command, check=True)
        if os.path.exists(output_file):
            print(f"Success: {output_file} created.")
            return output_file
        else:
            print(f"Error: Output file {output_file} was not created.")
            return None
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg error for {input_file}: {e}")
        return None


def create_file_list(tmp_dir):
    """Create a properly formatted file_list.txt for ffmpeg concatenation."""
    file_list_path = os.path.join(tmp_dir, "file_list.txt")

    # Print the tmp_dir contents for debugging
    print(f"Checking contents of {tmp_dir}:")
    for item in os.listdir(tmp_dir):
        print(f"Found: {item}")

    # More robust file detection
    preprocessed_files = []
    for root, _, files in os.walk(tmp_dir):
        for file in files:
            if file.startswith("pre_") and file.lower().endswith(".mp4"):
                full_path = Path(os.path.join(root, file))
                preprocessed_files.append(full_path)
    
    # Sort by creation time
    preprocessed_files.sort(key=lambda x: x.stat().st_ctime)
    
    print(f"Detected preprocessed files: {[str(file) for file in preprocessed_files]}")

    if not preprocessed_files:
        print(f"Error: No preprocessed files found in {tmp_dir}")
        return None

    try:
        with open(file_list_path, "w") as f:
            for video in preprocessed_files:
                f.write(f"file '{video.absolute()}'\n")

        print(f"File list created at {file_list_path} with contents:")
        with open(file_list_path, "r") as f:
            print(f.read())

        return file_list_path

    except Exception as e:
        print(f"Error creating file_list.txt: {e}")
        return None

def concatenate_videos(file_list, output_file):
    """Concatenate videos using ffmpeg."""
    command = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", file_list,
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        output_file,
    ]
    try:
        subprocess.run(command, check=True)
        print(f"Video concatenation completed: {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error concatenating videos: {e}")


def main():
    start_time = datetime.now()
    project_name = prompt_for_project_name()
    backup_subdir, tmp_dir = initialize_directories(project_name)

    print("Copying files...")
    new_files = copy_files(SOURCE_DIR, backup_subdir)
    if not new_files:
        print("No new files to process.")
        return

    print("Preprocessing videos...")
    # Use os.walk to find MP4 files recursively
    input_files = get_mp4_files(backup_subdir)
    print(f"Found the following MP4 files for preprocessing: {input_files}")

    if not input_files:
        print("No MP4 files found to preprocess.")
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(lambda f: preprocess_video(f, tmp_dir), input_files))

    if not any(results):
        print("No videos were successfully preprocessed.")
        return

    print("Creating file list...")
    file_list = create_file_list(tmp_dir)

    print("Concatenating videos...")
    output_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, output_file)

    print(f"Temporary files cleaned up from: {tmp_dir}")
    # shutil.rmtree(tmp_dir)

    elapsed_time = datetime.now() - start_time
    print(f"Total processing time: {elapsed_time}")


if __name__ == "__main__":
    main()
