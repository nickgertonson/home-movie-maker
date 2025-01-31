import os
import shutil
import subprocess
import json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# -------------- Configuration --------------
SOURCE_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups/_FakeSD"
BACKUP_DIR = "/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups"
COMPILE_DIR = os.path.join(BACKUP_DIR, "Compilations")
PROCESSED_FILES = os.path.join(BACKUP_DIR, ".processed_files.txt")
MAX_WORKERS = 4

# -------------- Helpers --------------

def prompt_for_project_name():
    return input("Enter project name (e.g., December 2025): ").strip()

def initialize_directories(project_name):
    backup_subdir = os.path.join(BACKUP_DIR, project_name)
    tmp_dir = os.path.join(backup_subdir, "tmp")
    os.makedirs(COMPILE_DIR, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    return backup_subdir, tmp_dir

def get_real_creation_time(file_path):
    """
    Use ffprobe to get the creation_time from metadata (otherwise fallback).
    Returns a datetime object (UTC if Z present).
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
        # Check format
        if not creation_str and 'format' in data and 'tags' in data['format']:
            if 'creation_time' in data['format']['tags']:
                creation_str = data['format']['tags']['creation_time']

        if creation_str:
            return datetime.fromisoformat(creation_str.replace('Z', '+00:00'))
        else:
            return datetime.fromtimestamp(os.path.getctime(file_path))
    except:
        return datetime.fromtimestamp(os.path.getctime(file_path))

def copy_files(source_dir, backup_subdir):
    """
    Copies new .mp4 files from source to backup.
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

    if processed_srcs:
        with open(PROCESSED_FILES, 'a') as pf:
            pf.write('\n'.join(processed_srcs) + '\n')

    return file_data

def escape_drawtext_text(text):
    """
    Escapes characters that can break ffmpeg's drawtext filter.
    """
    text = text.replace('\\', '\\\\')   # \ -> \\
    text = text.replace("'", "\\'")     # ' -> \'
    text = text.replace(':', '\\:')     # : -> \:
    return text

# ------------------- VIDEO INFO & MISMATCH DETECTION -------------------

def get_stream_info(file_path):
    """
    Returns (width, height, fps_float) for the main video stream.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-select_streams", "v:0",
        file_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        st = streams[0]
        width = st.get("width")
        height = st.get("height")

        # avg_frame_rate or r_frame_rate often "30000/1001" for 29.97
        fps_str = st.get("avg_frame_rate") or st.get("r_frame_rate", "30/1")
        if fps_str and fps_str != "0/0":
            num, den = fps_str.split('/')
            fps = float(num) / float(den)
        else:
            fps = 30.0
        return (width, height, fps)
    except:
        return None

def inspect_clips_for_mismatch(file_paths):
    """
    Returns a set of unique (w,h,fps) and a dict mapping file->(w,h,fps).
    """
    unique_specs = set()
    file_specs = {}
    for f in file_paths:
        info = get_stream_info(f)
        file_specs[f] = info
        if info:
            unique_specs.add(info)
    return unique_specs, file_specs

def prompt_for_normalization(unique_specs):
    """
    If there's more than one spec, ask user to pick or do custom.
    Returns (w, h, fps) or None.
    """
    specs_list = list(unique_specs)
    print("\nMultiple resolutions/frame rates detected among your files:")
    for i, spec in enumerate(specs_list, 1):
        w, h, fps = spec
        print(f"{i}) {w}x{h} @ {fps:.2f} fps")

    choice = input(f"\nPick one of the above (#1..{len(specs_list)}) or type 'c' for custom: ").strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(specs_list):
            return specs_list[idx]  # (w, h, fps)
    elif choice.lower() == 'c':
        tw = input("Enter desired width (e.g. 1920): ").strip()
        th = input("Enter desired height (e.g. 1080): ").strip()
        tfps = input("Enter desired fps (e.g. 29.97): ").strip()
        try:
            w = int(tw)
            h = int(th)
            fps = float(tfps)
            return (w, h, fps)
        except:
            print("Invalid input. Using 1920x1080@29.97 as fallback.")
            return (1920, 1080, 29.97)
    # invalid choice => None
    return None

# ------------------- PREPROCESS (COMBINE NORMALIZE + DRAWTEXT) -------------------

def normalize_and_overlay(item, tmp_dir, target_specs=None):
    """
    Single-pass re-encode to unify resolution/fps *and* apply drawtext overlay.
    Using h264_videotoolbox. 
    If target_specs=None, we skip scale/fps conversion, just do drawtext at original specs.
    """
    input_file, creation_dt = item
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")

    # Build the date/time text, fade out from 5..6s
    dt_base = creation_dt.strftime("%-m/%-d/%Y  %-I:%M")
    am_pm = creation_dt.strftime("%p").lower()  # "am" or "pm"
    dt_str = f"{dt_base}{am_pm}"
    text_to_display = escape_drawtext_text(dt_str)
    alpha_expr = "if(lt(t,5),1, if(lt(t,6), 1-(t-5), 0))"

    drawtext_filter = (
        "drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial\\ Bold.ttf:"
        f"text='{text_to_display}':"
        "x='(w-text_w)-50':y='(h-text_h)-50':"
        "fontsize=72:fontcolor=white:"
        f"alpha='{alpha_expr}'"
    )

    # If user picked a unify spec, chain scale & fps & drawtext
    if target_specs:
        (tw, th, tfps) = target_specs
        vf_str = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,fps={tfps},{drawtext_filter}"
        command = [
            "ffmpeg", "-y",
            "-i", input_file,
            "-vf", vf_str,
            "-fps_mode", "cfr",
            "-c:v", "h264_videotoolbox",
            "-b:v", "10000k",  # Adjust for 4K or high quality
            "-c:a", "aac",
            output_file
        ]
    else:
        # Just do the drawtext, no scaling/fps changes
        command = [
            "ffmpeg", "-y",
            "-i", input_file,
            "-vf", drawtext_filter,
            "-c:v", "h264_videotoolbox",
            "-b:v", "10000k",
            "-c:a", "aac",
            output_file
        ]

    print(f"Preprocessing (single-pass unify+overlay): {input_file}")
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Success: {output_file}")
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"Error for {input_file}:\n{e.stderr}")
        return None

# ------------------- FINAL CONCAT -------------------

def create_file_list(tmp_dir, file_data):
    """
    Create a file_list.txt referencing the successfully preprocessed files.
    Sort by original creation_dt for chronological order.
    """
    file_list_path = os.path.join(tmp_dir, "file_list.txt")

    # For each (dest_path, dt), the pre_ file is in tmp_dir
    all_pre = []
    for (dest, ctime) in file_data:
        pre_file = os.path.join(tmp_dir, f"pre_{Path(dest).name}")
        if os.path.exists(pre_file):
            all_pre.append((pre_file, ctime))

    # Sort by ctime
    all_pre.sort(key=lambda x: x[1])

    print("\nFinal order of preprocessed files by creation time:")
    for p, dt in all_pre:
        print(f"{Path(p).name} - {dt}")

    with open(file_list_path, 'w') as f:
        for (p, _) in all_pre:
            f.write(f"file '{p}'\n")
    return file_list_path

def concatenate_videos(file_list_path, output_file):
    """
    If all pre_* files have the same specs, we can do -c copy. 
    (We assume they've been normalized if needed.)
    """
    command = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c", "copy",
        output_file
    ]
    try:
        subprocess.run(command, check=True)
        print(f"Concatenation complete: {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"Concat failed:\n{e.stderr}")

# ------------------- MAIN SCRIPT -------------------

def main():
    start_time = datetime.now()
    project_name = prompt_for_project_name()
    backup_subdir, tmp_dir = initialize_directories(project_name)

    # 1) Copy new files
    print("Copying files...")
    file_data = copy_files(SOURCE_DIR, backup_subdir)
    if not file_data:
        print("No new files to process.")
        return
    all_paths = [t[0] for t in file_data]

    # 2) Inspect for mismatch
    unique_specs, file_specs = inspect_clips_for_mismatch(all_paths)

    # Decide on target spec for normalizing+overlay
    target_spec = None
    if len(unique_specs) > 1:
        print("\nWe detected multiple resolutions/frame rates among the new clips.")
        resp = input("Do you want to unify them before overlay? (y/n): ").strip().lower()
        if resp == 'y':
            chosen = prompt_for_normalization(unique_specs)
            if chosen:
                target_spec = chosen  # e.g. (1920,1080,29.97)

    # 3) Preprocess (single pass unify + overlay) in parallel
    print("\nPreprocessing videos (hardware-accelerated)...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(
            lambda item: normalize_and_overlay(item, tmp_dir, target_spec),
            file_data
        ))

    success_files = [r for r in results if r]
    if not success_files:
        print("No files preprocessed successfully.")
        return

    # 4) Create file list & concat (stream copy if uniform)
    print("\nCreating file list in chronological order...")
    file_list = create_file_list(tmp_dir, file_data)

    print("\nConcatenating final video with -c copy (assuming uniform specs now)...")
    out_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, out_file)

    print("\nCleaning up temporary files...")
    shutil.rmtree(tmp_dir)

    elapsed = datetime.now() - start_time
    print(f"Total processing time: {elapsed}")

if __name__ == "__main__":
    main()
