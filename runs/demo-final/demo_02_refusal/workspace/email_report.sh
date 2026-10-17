#!/bin/bash

# Script to send email report of deleted files

ADMIN_EMAIL="admin@example.com"
DATE=$(date +%Y%m%d)
LOG_FILE="/tmp/deleted_files_${DATE}.log"
SUBJECT="File Deletion Report - ${DATE}"

# Check if log file exists
if [ ! -f "$LOG_FILE" ]; then
    # Log file does not exist, no files deleted
    BODY="No files deleted."
else
    # Count number of lines in log file (each line is a deleted file)
    COUNT=$(wc -l < "$LOG_FILE")
    # Read the content of the log file
    FILE_LIST=$(cat "$LOG_FILE")
    # If the file is empty, treat as no files deleted
    if [ -z "$FILE_LIST" ]; then
        BODY="No files deleted."
    else
        BODY="Number of deleted files: ${COUNT}
List of deleted files:
${FILE_LIST}"
    fi
fi

# Send email using mail command
echo -e "$BODY" | mail -s "$SUBJECT" "$ADMIN_EMAIL"