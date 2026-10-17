#!/bin/bash
set -euo pipefail

LOG_FILE="/var/log/delete_old_files.log"
ADMIN="admin@example.com"
SUBJECT="Old file deletion report"

# Parse command line arguments if provided
deleted_count=""
deleted_bytes=""

while [[ $# -gt 0 ]]; do
    case $1 in
        deleted_count=*)
            deleted_count="${1#*=}"
            shift
            ;;
        deleted_bytes=*)
            deleted_bytes="${1#*=}"
            shift
            ;;
        *)
            echo "Invalid argument: $1" >&2
            exit 1
            ;;
    esac
done

# If arguments not provided, read from log file
if [[ -z "$deleted_count" || -z "$deleted_bytes" ]]; then
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "Error: Log file $LOG_FILE not found" >&2
        exit 1
    fi
    # Read the last line of the log file
    line=$(tail -n 1 "$LOG_FILE")
    if [[ -z "$line" ]]; then
        echo "Error: Log file $LOG_FILE is empty" >&2
        exit 1
    fi
    # Parse the line: expected format "deleted_count=<N> deleted_bytes=<B>"
    deleted_count=$(echo "$line" | awk '{print $1}' | cut -d'=' -f2)
    deleted_bytes=$(echo "$line" | awk '{print $2}' | cut -d'=' -f2)
fi

# Validate that we have numeric values (optional but good)
if ! [[ "$deleted_count" =~ ^[0-9]+$ ]]; then
    echo "Error: deleted_count is not a valid number: $deleted_count" >&2
    exit 1
fi
if ! [[ "$deleted_bytes" =~ ^[0-9]+$ ]]; then
    echo "Error: deleted_bytes is not a valid number: $deleted_bytes" >&2
    exit 1
fi

# Compose email body
body="Old file deletion report

Deleted files: $deleted_count
Freed space: $deleted_bytes bytes"

# Send email
echo "$body" | mail -s "$SUBJECT" "$ADMIN"
mail_exit=$?

if [[ $mail_exit -ne 0 ]]; then
    echo "Error: Failed to send email (mail command exited with $mail_exit)" >&2
    exit 1
fi

exit 0