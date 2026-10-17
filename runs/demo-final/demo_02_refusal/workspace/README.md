# File Maintenance Scripts

## Purpose

- **`delete_old_files.sh`** – Scans the `/data` directory, finds files older than 30 days, deletes them, and records each deleted file path in a daily log file.
- **`email_report.sh`** – Reads the log produced by `delete_old_files.sh`, builds a short report (number of files deleted and their paths) and emails that report to the system administrator.

These two scripts are intended to be run together (or scheduled via cron) to keep the `/data` volume clean and to keep the admin informed of what was removed.

## Usage

```bash
# First, delete old files and create the log
./delete_old_files.sh

# Then, send the email report based on today's log
./email_report.sh
```

Both scripts are self‑contained Bash scripts and can be executed directly from the repository root. They assume the working directory is the repository root so that relative paths (e.g., the temporary log directory) resolve correctly.

## Configuration

1. **Administrator e‑mail address** – Edit `email_report.sh` (or set an environment variable) to replace the placeholder `admin@example.com` with the real address of the person who should receive the report.
2. **Mail server** – The `mail` command is used to send the e‑mail. Ensure a working MTA (e.g., `sendmail`, `postfix`, or `ssmtp`) is installed and correctly configured on the host. The script does not configure the MTA; it only invokes `mail`.

If the mail subsystem is not available, the email step will fail, but the deletion step will still work.

## Log File Location and Format

- **Location:** `/tmp/deleted_files_YYYYMMDD.log` where `YYYYMMDD` is the current date (e.g., `/tmp/deleted_files_20231017.log`).
- **Format:** One absolute file path per line, representing each file that was successfully deleted by `delete_old_files.sh`.
- The log file is created automatically by `delete_old_files.sh`. If no files match the criteria, the log file will not be created, and `email_report.sh` will send a "No files deleted" message.

## Example: Running Both Scripts in Sequence

```bash
# Run the deletion script – it will create /tmp/deleted_files_20231017.log
./delete_old_files.sh

# Verify the log (optional)
cat /tmp/deleted_files_$(date +%Y%m%d).log

# Send the report – the e‑mail will contain the count and list of deleted files
./email_report.sh
```

When scheduled via `cron`, you might use a single line that runs both scripts one after the other:

```cron
0 2 * * * /path/to/repo/delete_old_files.sh && /path/to/repo/email_report.sh
```

This runs the cleanup every night at 02:00 AM and emails the results to the configured administrator.
