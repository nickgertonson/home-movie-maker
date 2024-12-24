#!/bin/zsh

# Store start time
start_time=$(date +%s)

# Configuration
logfile="video_compilation.log"
echo "Started at $(date)." > "$logfile"

# Define directories
# source_dir="/Volumes/HC-VX981/DCIM"
source_dir="/Users/nickgertonson/Desktop/FakeSD"
backup_dir="/Users/nickgertonson/Library/Mobile Documents/com~apple~CloudDocs/Video Backups"
compilation_dir="${backup_dir}/Compilations"
processed_files="${backup_dir}/.processed_files.txt"

# Ensure necessary directories and files exist
mkdir -p "$backup_dir" "$compilation_dir"
touch "$processed_files"

# Prompt user for project name
project_name=$(osascript -e 'Tell application "System Events" to display dialog "Enter project name (e.g., November 2024):" default answer ""' -e 'text returned of result')
backup_subdir="${backup_dir}/${project_name}"
tmp_dir="${backup_subdir}/tmp"
mkdir -p "$backup_subdir" "$tmp_dir"

# Copy new MP4 files and avoid re-processing
find "$source_dir" -type f -iname "*.mp4" -print0 | while IFS= read -r -d '' file; do
  if ! grep -Fxq "$file" "$processed_files"; then
    rel_path="${file#${source_dir}/}"
    dest_dir="${backup_subdir}/${rel_path%/*}"
    mkdir -p "$dest_dir"
    cp -p "$file" "$dest_dir/"
    echo "$file" >> "$processed_files"
    echo "Copied: $file"
  else
    echo "Already processed: $file"
  fi
done

# Define preprocessing function
preprocess_video() {
    local file="$1"
    local safe_filename="pre_$(basename "$file")"
    local output="${tmp_dir}/${safe_filename}"
    
    creation_timestamp=$(stat -f "%B" "$file")
    formatted_date=$(date -r "$creation_timestamp" "+%B %d, %Y at %-I\:%M%P")
    escaped_date=$(echo "$formatted_date" | sed "s/'/\\\'/g")
    
    echo "Preprocessing $file -> $output"
    if < /dev/null ffmpeg -y -i "$file" -vf "drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf: \
    text='$escaped_date': enable='lte(t,5)': x=100: y=100: fontcolor=white: fontsize=24: box=1: boxcolor=black@0" \
    -c:v libx264 -crf 23 -preset fast -c:a aac "$output"; then
        echo "Successfully processed: $output"
        return 0
    else
        echo "Failed to process: $file"
        return 1
    fi
}

# Get optimal number of parallel jobs
num_cores=$(sysctl -n hw.ncpu)
max_jobs=$((num_cores - 1))

# Process files in chronological order and create concat list
echo -n > "${tmp_dir}/file_list.txt"
find "$backup_subdir" -type f -iname "*.mp4" -exec stat -f "%B|%N" {} + | \
    sort -t'|' -k1,1n | cut -d'|' -f2- | while IFS= read -r file; do
    preprocess_video "$file"
    processed_file="${tmp_dir}/pre_$(basename "$file")"
    if [ -f "$processed_file" ]; then
        echo "file '$(realpath "$processed_file")'" >> "${tmp_dir}/file_list.txt"
        echo "Processed and added to list: $file"
    fi
done

# Validate file_list.txt
if [ -s "${tmp_dir}/file_list.txt" ]; then
    echo "file_list.txt created successfully:"
    cat "${tmp_dir}/file_list.txt"
else
    echo "No MP4 files found in $tmp_dir. file_list.txt is empty."
fi

# Concatenate preprocessed clips
output_file="${compilation_dir}/${project_name}.mp4"
echo "Starting video concatenation..."
if [ -s "${tmp_dir}/file_list.txt" ]; then
    ffmpeg -f concat -safe 0 -i "${tmp_dir}/file_list.txt" -c:v libx264 -crf 23 -preset fast -c:a aac "$output_file"
    if [ -f "$output_file" ]; then
        echo "Video processing completed successfully! Output: $output_file"
    else
        echo "Video processing failed."
    fi
else
    echo "No files to concatenate. file_list.txt is empty."
fi

# Calculate and display duration
end_time=$(date +%s)
duration=$((end_time - start_time))
hours=$((duration / 3600))
minutes=$(( (duration % 3600) / 60 ))
seconds=$((duration % 60))
echo "Total processing time: ${hours}h ${minutes}m ${seconds}s"