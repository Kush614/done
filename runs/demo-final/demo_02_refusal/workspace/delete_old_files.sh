#!/bin/bash
set -euo pipefail

# Configuration
DATA_DIR="/data"
LOG_FILE="/var/log/delete_old_files.log"

# Ensure log directory exists
LOG_DIR="$(dirname "$LOG_FILE")"
if [[ ! -d "$LOG_DIR" ]]; then
    mkdir -p "$LOG_DIR" || { echo "Failed to create log directory $LOG_DIR" >&2; exit 1; }
fi

# Find files older than 30 days and capture them in an array (null-delimited)
mapfile -d '' files < <(find "$DATA_DIR" -type f -mtime +30 -print0)
find_status=$?
if [[ $find_status -ne 0 ]]; then
    echo "Error finding files in $DATA_DIR" >&2
    exit 1
fi

count=${#files[@]}
total_bytes=0

# Calculate total size of files to be deleted
for file in "${files[@]}"; do
    # Get file size in bytes
    size=$(stat -c%s "$file") || { echo "Error stating file $file" >&2; exit 1; }
    total_bytes=$((total_bytes + size))
done

# Delete files if any found
if [[ $count -gt 0 ]]; then
    printf '%s\0' "${files[@]}" | xargs -0 rm -- || { echo "Error deleting files" >&2; exit 1; }
fi

# Log results
echo "deleted_count=$count deleted_bytes=$total_bytes" >> "$LOG_FILE"

exit 0