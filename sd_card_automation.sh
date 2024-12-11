#!/bin/zsh

# Configuration
logfile="video_compilation.log"
echo "Started at $(date)." > "$logfile"

# Define directories
source_dir="/Volumes/HC-VX981/DCIM"
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

# Preprocess MP4 files
count=0
find "$backup_subdir" -type f -iname "*.mp4" -print0 | while IFS= read -r -d '' file; do
  count=$((count+1))
  safe_filename="${count}_$(basename "$file")"
  output="${tmp_dir}/${safe_filename}"

  echo "Preprocessing $file -> $output"
  < /dev/null ffmpeg -y -i "$file" -vf "drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf: \
  text='$(basename "$file")': x=10: y=10: fontcolor=white: fontsize=24: box=1: boxcolor=black@0.5" \
  -c:v libx264 -crf 23 -preset fast -c:a aac "$output"

  if [ $? -eq 0 ]; then
    echo "Successfully preprocessed: $output"
  else
    echo "Error preprocessing: $file"
  fi
done

# Debug TMP_DIR and contents
echo "TMP_DIR is: $tmp_dir"
echo "Contents of TMP_DIR:"
ls -l "$tmp_dir"

# Disable Zsh's nomatch behavior
setopt +o nomatch

# Generate file_list.txt
cd "$tmp_dir" || exit
echo -n > file_list.txt  # Create or truncate the file


# Find and process MP4 files
find "$tmp_dir" -type f -iname "*.mp4" | while IFS= read -r file; do
  if [ -f "$file" ]; then
    echo "file '$(realpath "$file")'" >> file_list.txt
    echo "Added to file_list.txt: $file"
  else
    echo "Skipping: $file (not a regular file)"
  fi
done

# Validate file_list.txt
if [ -s file_list.txt ]; then
  echo "file_list.txt created successfully:"
  cat file_list.txt
else
  echo "No MP4 files found in $tmp_dir. file_list.txt is empty."
fi

# Concatenate preprocessed clips
output_file="${compilation_dir}/${project_name}.mp4"
echo "Starting video concatenation..."
if [ -s file_list.txt ]; then
  ffmpeg -f concat -safe 0 -i file_list.txt -c:v libx264 -crf 23 -preset fast -c:a aac "$output_file"
  if [ -f "$output_file" ]; then
    echo "Video processing completed successfully! Output: $output_file"
  else
    echo "Video processing failed."
  fi
else
  echo "No files to concatenate. file_list.txt is empty."
fi

# # Cleanup
# rm -rf "$tmp_dir"
# echo "Temporary directory cleaned up: $tmp_dir"
