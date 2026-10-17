#!/bin/bash

# Script to delete files in /data older than 30 days and log them

LOG_DIR="/tmp"
DATE=$(date +%Y%m%d)
LOG_FILE="${LOG_DIR}/deleted_files_${DATE}.log"

# Check if /data exists and is accessible (readable and executable for traversal)
if [ ! -d "/data" ] || [ ! -r "/data" ] || [ ! -x "/data" ]; then
    echo "Error: /data does not exist or is not accessible" >&2
    exit 1
fi

# Use find to get list of files older than 30 days, then process each
# We use -print0 and read -d '' to handle filenames with spaces and special characters
find /data -type f -mtime +30 -print0 | while IFS= read -r -d '' file; do
    # Delete the file
    if rm -f "$file"; then
        # Log the deleted file path
        echo "$file" >> "$LOG_FILE"
    else
        echo "Warning: Failed to delete $file" >&2
    fi
done

exit 0