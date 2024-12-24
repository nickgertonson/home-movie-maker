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

# Preprocess MP4 files
# find "$backup_subdir" -type f -iname "*.mp4" -print0 | while IFS= read -r -d '' file; do
find "$backup_subdir" -type f -iname "*.mp4" -exec stat -f "%B|%N" {} + | sort -t'|' -k1,1n | while IFS='|' read -r creation_timestamp file; do
  safe_filename="pre_$(basename "$file")"
  output="${tmp_dir}/${safe_filename}"

  creation_timestamp=$(stat -f "%B" "$file")

  # Format the date as "Month, DD, YYYY 8:50pm"
  formatted_date=$(date -r "$creation_timestamp" "+%B %d, %Y at %-I\:%M%P")

  echo "Made On $formatted_date"

  escaped_date=$(echo "$formatted_date" | sed "s/'/\\\'/g")

  echo "Preprocessing $file -> $output"
  < /dev/null ffmpeg -y -i "$file" -vf "drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf: \
  text='$escaped_date': x=100: y=100: fontcolor=white: fontsize=24: box=1: boxcolor=black@0" \
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


# # Find and process MP4 files
# find "$tmp_dir" -type f -iname "*.mp4" | while IFS= read -r file; do
#   if [ -f "$file" ]; then
#     echo "file '$(realpath "$file")'" >> file_list.txt
#     echo "Added to file_list.txt: $file"
#   else
#     echo "Skipping: $file (not a regular file)"
#   fi
# done

# Find files, retrieve creation times, and sort them
find "$tmp_dir" -type f -iname "*.mp4" -exec stat -f "%B|%N" {} + | sort -t'|' -k1,1n | cut -d'|' -f2- | while IFS= read -r file; do
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

# Cleanup
# rm -rf "$tmp_dir"
# echo "Temporary directory cleaned up: $tmp_dir"


# Calculate and display duration at the end
end_time=$(date +%s)
duration=$((end_time - start_time))
hours=$((duration / 3600))
minutes=$(( (duration % 3600) / 60 ))
seconds=$((duration % 60))

echo "Total processing time: ${hours}h ${minutes}m ${seconds}s"
echo "Total processing time: ${hours}h ${minutes}m ${seconds}s" >> "$logfile"