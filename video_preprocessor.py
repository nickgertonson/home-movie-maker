import os
import shutil
import subprocess
import json
import re
import time
import threading
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from tqdm import tqdm

# -------------- Configuration --------------
SOURCE_DIR = Path("/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups/_FakeSD")
BACKUP_DIR = Path("/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups")
COMPILE_DIR = BACKUP_DIR / "Compilations"
PROCESSED_FILES = BACKUP_DIR / ".processed_files.txt"
MAX_WORKERS = 4

# Global progress dictionary and lock for per-video progress
progress_dict = {}
progress_lock = threading.Lock()
all_done = False  # Flag to signal progress updater to finish

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
        if 'streams' in data:
            for stream in data['streams']:
                if 'tags' in stream and 'creation_time' in stream['tags']:
                    creation_str = stream['tags']['creation_time']
                    break
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
    text = text.replace('\\', '\\\\')
    text = text.replace("'", "\\'")
    text = text.replace(':', '\\:')
    return text

def get_video_duration(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        tqdm.write(f"Error retrieving duration for {file_path}: {e}")
        return None

def get_stream_info(file_path):
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
        raw_fps = st.get("avg_frame_rate") or st.get("r_frame_rate", "30/1")
        try:
            num, den = raw_fps.split('/')
            fps = float(num) / float(den)
        except Exception:
            fps = 30.0
            raw_fps = "30/1"
        return (width, height, fps, raw_fps)
    except Exception:
        return None

def inspect_clips_for_mismatch(file_paths):
    unique_specs = set()
    file_specs = {}
    for f in file_paths:
        info = get_stream_info(f)
        file_specs[f] = info
        if info:
            unique_specs.add(info)
    return unique_specs, file_specs

def prompt_for_normalization(unique_specs):
    specs_list = list(unique_specs)
    tqdm.write("\nMultiple resolutions/frame rates detected among your files:")
    for i, spec in enumerate(specs_list, 1):
        w, h, fps, _ = spec
        tqdm.write(f"{i}) {w}x{h} @ {fps:.2f} fps")
    choice = input(f"\nPick one of the above (#1..{len(specs_list)}) or type 'c' for custom: ").strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(specs_list):
            w, h, fps, fps_raw = specs_list[idx]
            return (w, h, fps, fps_raw)
    elif choice.lower() == 'c':
        tw = input("Enter desired width (e.g. 1920): ").strip()
        th = input("Enter desired height (e.g. 1080): ").strip()
        tfps = input("Enter desired fps (e.g. 29.97 or 30000/1001): ").strip()
        try:
            w = int(tw)
            h = int(th)
            if '/' in tfps:
                num, den = tfps.split('/')
                fps_float = float(num) / float(den)
            else:
                fps_float = float(tfps)
            return (w, h, fps_float, tfps)
        except:
            tqdm.write("Invalid input. Using 1920x1080@29.97 as fallback.")
            return (1920, 1080, 29.97, "30000/1001")
    return None

# ------------------- PREPROCESS (COMBINE NORMALIZE + DRAWTEXT) -------------------

def normalize_and_overlay(item, tmp_dir, target_specs=None, encoder="h264_videotoolbox", bitrate="60000k", target_hw_fps=None):
    input_file, creation_dt = item
    output_file = os.path.join(tmp_dir, f"pre_{Path(input_file).name}")
    total_duration = get_video_duration(input_file)
    if total_duration is None or total_duration <= 0:
        total_duration = 1.0

    with progress_lock:
        progress_dict[input_file] = 0.0

    dt_base = creation_dt.strftime("%-m/%-d/%Y  %-I:%M")
    am_pm = creation_dt.strftime("%p").lower()
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

    if target_specs:
        (tw, th, tfps, tfps_raw) = target_specs
        video_filter = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,fps={tfps},{drawtext_filter}"
        fps_value = tfps_raw
    else:
        info = get_stream_info(input_file)
        if info:
            original_fps = info[2]
            original_fps_str = info[3]
        else:
            original_fps = 29.97
            original_fps_str = "30000/1001"
        video_filter = drawtext_filter
        fps_value = original_fps_str

    if encoder == "h264_videotoolbox" and target_hw_fps is not None:
        fps_value = str(target_hw_fps)

    command = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-vf", video_filter,
        "-r", fps_value,
        "-fps_mode", "cfr",
        "-vsync", "cfr",
        "-c:v", encoder,
        "-b:v", bitrate,
        "-c:a", "aac",
        "-progress", "pipe:1",
        output_file
    ]

    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        while True:
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue
            
            m = re.match(r"out_time_ms=(\d+)", line)
            if m:
                out_time_us = int(m.group(1))
                new_time = out_time_us / 1_000_000.0
                with progress_lock:
                    progress_dict[input_file] = min(new_time, total_duration)
            
            if line.strip() == "progress=end":
                with progress_lock:
                    progress_dict[input_file] = total_duration
                break

        process.wait()
    except Exception as e:
        tqdm.write(f"Error processing {input_file}: {str(e)}")
    finally:
        with progress_lock:
            progress_dict[input_file] = total_duration
        
        if process and process.poll() is None:
            process.kill()
            tqdm.write(f"Force-killed stalled process for {input_file}")

    if process and process.returncode == 0:
        tqdm.write(f"Success: {output_file}")
        return output_file
    else:
        if process:
            stderr_output = process.stderr.read()
            tqdm.write(f"Error processing {input_file}:\n{stderr_output}")
        return None

# ------------------- FINAL CONCAT -------------------

def create_file_list(tmp_dir, file_data):
    file_list_path = os.path.join(tmp_dir, "file_list.txt")
    all_pre = []
    for (dest, ctime) in file_data:
        pre_file = os.path.join(tmp_dir, f"pre_{Path(dest).name}")
        if os.path.exists(pre_file):
            all_pre.append((pre_file, ctime))
    all_pre.sort(key=lambda x: x[1])
    tqdm.write("\nFinal order of preprocessed files by creation time:")
    for p, dt in all_pre:
        tqdm.write(f"{Path(p).name} - {dt}")
    with open(file_list_path, 'w') as f:
        for (p, _) in all_pre:
            f.write(f"file '{p}'\n")
    return file_list_path

def concatenate_videos(file_list_path, output_file):
    command = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", file_list_path,
        "-c", "copy",
        output_file
    ]
    try:
        subprocess.run(command, check=True)
        tqdm.write(f"Concatenation complete: {output_file}")
    except subprocess.CalledProcessError as e:
        tqdm.write(f"Concat failed:\n{e.stderr}")

# ------------------- OVERALL PROGRESS UPDATER -------------------

def overall_progress_updater(total_all):
    overall_pbar = tqdm(total=total_all, desc="Overall Progress", unit="s",
                          bar_format="{l_bar}{bar}| {n:.0f}s/{total:.0f}s [{elapsed}<{remaining}, {rate_fmt}]")
    while not all_done:
        with progress_lock:
            current = sum(progress_dict.get(k, 0) for k in progress_dict)
        overall_pbar.n = current
        overall_pbar.refresh()
        time.sleep(0.5)
    with progress_lock:
        current = sum(progress_dict.get(k, 0) for k in progress_dict)
    overall_pbar.n = current
    overall_pbar.refresh()
    overall_pbar.close()

# ------------------- MAIN SCRIPT -------------------

def main():
    global all_done
    start_time = datetime.now()
    project_name = prompt_for_project_name()
    backup_subdir, tmp_dir = initialize_directories(project_name)
    tqdm.write("Copying files...")
    file_data = copy_files(SOURCE_DIR, backup_subdir)
    if not file_data:
        tqdm.write("No new files to process.")
        return
    all_paths = [t[0] for t in file_data]
    unique_specs, file_specs = inspect_clips_for_mismatch(all_paths)
    target_spec = None
    if len(unique_specs) > 1:
        tqdm.write("\nWe detected multiple resolutions/frame rates among the new clips.")
        resp = input("Do you want to unify them before overlay? (y/n): ").strip().lower()
        if resp == 'y':
            chosen = prompt_for_normalization(unique_specs)
            if chosen:
                target_spec = chosen

    encoder_choice = input("Choose encoder: hardware (h) or software (s): ").strip().lower()
    if encoder_choice == "s":
        encoder = "libx264"
        bitrate = "60000k"
        target_hw_fps = None
    else:
        encoder = "h264_videotoolbox"
        bitrate = "60000k"
        target_hw_fps = input("Enter target FPS for hardware encoding (30 or 60): ").strip()
        try:
            target_hw_fps = int(target_hw_fps)
            if target_hw_fps not in (30, 60):
                tqdm.write("Invalid FPS, defaulting to 30fps.")
                target_hw_fps = 30
        except:
            tqdm.write("Invalid input, defaulting to 30fps.")
            target_hw_fps = 30

    total_all = 0.0
    for (dest, _) in file_data:
        dur = get_video_duration(dest)
        if dur:
            total_all += dur

    tqdm.write("\nPreprocessing videos...")
    progress_thread = threading.Thread(target=overall_progress_updater, args=(total_all,))
    progress_thread.start()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(normalize_and_overlay, item, tmp_dir, target_spec, encoder, bitrate, target_hw_fps)
                   for item in file_data]
        results = []
        try:
            for future in as_completed(futures, timeout=3600):
                try:
                    results.append(future.result())
                except Exception as e:
                    tqdm.write(f"Task failed: {str(e)}")
        except TimeoutError:
            tqdm.write("Warning: Some tasks exceeded timeout limit")

    all_done = True
    progress_thread.join()

    success_files = [r for r in results if r]
    if not success_files:
        tqdm.write("No files preprocessed successfully.")
        return

    tqdm.write("\nCreating file list in chronological order...")
    file_list = create_file_list(tmp_dir, file_data)

    tqdm.write("\nConcatenating final video with -c copy (assuming uniform specs now)...")
    out_file = os.path.join(COMPILE_DIR, f"{project_name}.mp4")
    concatenate_videos(file_list, out_file)

    tqdm.write("\nCleaning up temporary files...")
    shutil.rmtree(tmp_dir)

    elapsed = datetime.now() - start_time
    tqdm.write(f"Total processing time: {elapsed}")

if __name__ == "__main__":
    main()