import os
import shutil
import subprocess
import json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ------------------- CONFIGURATION -------------------
SOURCE_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups/_FakeSD"
BACKUP_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups"
COMPILE_DIR = os.path.join(BACKUP_DIR, "Compilations")
PROCESSED_FILES = os.path.join(BACKUP_DIR, ".processed_files.txt")
MAX_WORKERS = 4

# ------------------- USER PROMPTS -------------------

def prompt_for_project_name():
    return input("Enter project name (e.g., December 2025): ").strip()

def prompt_for_framerate():
    """
    Asks user to choose from three options:
     1) 29.97
     2) 59.94
     3) Custom frame rate
    Returns a string like "29.97" or the user-entered rate.
    """
    print("\nChoose a final frame rate:")
    print("1) 29.97 fps")
    print("2) 59.94 fps")
    print("3) Enter your own (e.g., 30, 60, 23.976, etc.)")
    choice = input("Enter your choice (1/2/3): ").strip()

    if choice == "1":
        return "29.97"
    elif choice == "2":
        return "59.94"
    elif choice == "3":
        custom = input("Enter custom frame rate (e.g., 30, 60, 23.976): ").strip()
        return custom if custom else "29.97"  # fallback if user leaves blank
    else:
        print("Invalid choice, defaulting to 29.97 fps.")
        return "29.97"

# ------------------- HELPER FUNCTIONS -------------------

def initialize_directories(project_name):
    backup_subdir = os.path.join(BACKUP_DIR, project_name)
    tmp_dir = os.path.join(backup_subdir, "tmp")

    # Ensure the compilation directory exists
    os.makedirs(COMPILE_DIR, exist_ok=True)
    # Ensure the project subdir & tmp subdir exist
    os.makedirs(tmp_dir, exist_ok=True)

    return backup_subdir, tmp_dir

def get_real_creation_time(file_path):
    """
    Use ffprobe to get the creation_time from the file's metadata.
    If not found, fall back to os.path.getctime().
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

        creation_str = None

        # Check streams
        if 'streams' in data:
            for stream in data['streams']:
                if 'tags' in stream and 'creation_time' in stream['tags']:
                    creation_str = stream['tags']['creation_time']
                    break

        # If still None, check format tags
        if not creation_str and 'format' in data and 'tags' in data['format']:
            if 'creation_time' in data['format']['tags']:
                creation_str = data['format']['tags']['creation_time']

        if creation_str:
            # Typically an ISO8601 string like "2025-01-28T12:34:56.000000Z"
            return datetime.fromisoformat(creation_str.replace('Z','+00:00'))
        else:
            return datetime.fromtimestamp(os.path.getctime(file_path))

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError):
        return datetime.fromtimestamp(os.path.getctime(file_path))

def copy_files(source_dir, backup_subdir):
    """
    Copies new .mp4 files from source_dir to backup_subdir,
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

                    shutil.copy2(src, dest)

                    creation_dt = get_real_creation_time(src)
                    file_data.append((dest, creation_dt))
                    processed_srcs.append(src)

    # Update .processed_files so we don't copy these again
    if processed_srcs:
        with open(PROCESSED_FILES, 'a') as pf:
            pf.write('\n'.join(processed_srcs) + '\n')

    return file_data  # [(dest_path, datetime), ...]

def escape_drawtext_text(text):
    """
    Escapes characters that can break ffmpeg's drawtext filter.
    """
    text = text.replace('\\', '\\\\')   # \ -> \\
    text = text.replace("'", "\\'")     # ' -> \'
    text = text.replace(':', '\\:')     # : -> \:
    return text

def preprocess_video(item, tmp_dir):
    """
    Overlays the date/time onto each video using drawtext (with a fade-out),
    then encodes with h264_videotoolbox for GPU-accelerated processing.
    """
    input_file, creation_dt = item
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")

    # Example: "12/25/2025  4:18pm"
    dt_base = creation_dt.strftime("%-m/%-d/%Y  %-I:%M")
    am_pm = creation_dt.strftime("%p").lower()  # "am"/"pm"
    dt_str = f"{dt_base}{am_pm}"

    text_to_display = escape_drawtext_text(dt_str)

    # Fade out from 5s to 6s
    alpha_expr = "if(lt(t,5),1, if(lt(t,6), 1-(t-5), 0))"

    # Use Arial Bold (adjust font path if needed)
    drawtext_filter = (
        "drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial\\ Bold.ttf:"
        f"text='{text_to_display}':"
        "x='(w-text_w)-50':y='(h-text_h)-50':"
        "fontsize=72:fontcolor=white:"
        f"alpha='{alpha_expr}'"
    )

    # Switch from libx264 to hardware-accelerated "h264_videotoolbox"
    # You'll want to specify a target bitrate (e.g. 10 Mbps) for 4K. Adjust as needed:
    command = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-vf", drawtext_filter,
        "-c:v", "h264_videotoolbox",
        "-b:v", "10000k",  # Example ~10 Mbps. Increase if you need higher quality for 4K
        "-c:a", "aac",
        output_file
    ]

    print(f"Preprocessing (GPU-accelerated): {input_file}")
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Success: {output_file} created.")
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"Error preprocessing {input_file}:\n{e.stderr}")
        return None

def create_file_list(tmp_dir, file_data):
    """
    Creates file_list.txt sorted by real creation datetime.
    Only includes files that were successfully preprocessed.
    """
    file_list_path = os.path.join(tmp_dir, "file_list.txt")

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

    # Write out the concat list
    with open(file_list_path, 'w') as f:
        for video, _ in processed_files:
            f.write(f"file '{video}'\n")

    return file_list_path

def concatenate_videos(file_list_path, output_file, chosen_framerate):
    """
    Concatenate all preprocessed videos by re-encoding,
    applying the user-chosen frame rate at constant intervals (cfr).
    """
    command = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac",
        # Force user-chosen fps at constant frame intervals
        "-r", chosen_framerate,
        "-fps_mode", "cfr",
        output_file
    ]
    try:
        subprocess.run(command, check=True)
        print(f"Concatenation complete: {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Concatenation failed:\n{e.stderr}")

# ------------------- MAIN SCRIPT -------------------

def main():
    start_time = datetime.now()
    project_name = prompt_for_project_name()
    chosen_framerate = prompt_for_framerate()

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

    print("\nConcatenating videos (re-encoding)...")
    output_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, output_file, chosen_framerate)

    print("\nCleaning up temporary files...")
    shutil.rmtree(tmp_dir)

    elapsed = datetime.now() - start_time
    print(f"Total processing time: {elapsed}")

if __name__ == "__main__":
    main()
