# Old File Deletion and Reporting Scripts

## Overview

This repository contains two Bash scripts that work together to clean up old files in `/data` and notify an administrator about the cleanup:

1. **`delete_old_files.sh`** – Finds and deletes files older than 30 days, then writes a summary log.
2. **`email_report.sh`** – Reads the summary log (or accepts values via arguments) and sends a plain‑text email report.

Both scripts are deliberately simple and rely only on standard GNU utilities that are typically available on most Linux distributions.

---

## Prerequisites / Dependencies

- **GNU `find`** – Used by `delete_old_files.sh` to locate files by modification time.
- **`mail` (mailx)** – Used by `email_report.sh` to send the email. Ensure the `mail` command is installed and correctly configured to use your system's SMTP relay or mail server.
- **Write permission** to `/var/log/delete_old_files.log` for the user executing the scripts.
- **Read permission** to `/data` for the user executing the scripts.

---

## Configuration

1. **Administrator email address** – Edit `email_report.sh` (or set an environment variable) to specify the address that should receive the report. The placeholder in the script is `ADMIN_EMAIL="admin@example.com"`. Replace `admin@example.com` with the real address.
2. **Mail server** – The `mail` command uses the system's mail configuration (e.g., `/etc/mail.rc`, `/etc/ssmtp/ssmtp.conf`, or `postfix` settings). Ensure that the host can send outbound mail to the administrator's domain.

---

## Usage

### 1. Delete old files

```bash
chmod +x delete_old_files.sh
./delete_old_files.sh
```

The script will:
- Search `/data` for regular files (`-type f`) with a modification time older than 30 days (`-mtime +30`).
- Delete those files.
- Write a single‑line log entry to `/var/log/delete_old_files.log` in the format:

```
deleted_count=<N> deleted_bytes=<B>
```

where `<N>` is the number of files removed and `<B>` is the total number of bytes freed.

The script exits with status `0` on success; any failure during the `find`/`delete` process results in a non‑zero exit code.

### 2. Send the email report

```bash
chmod +x email_report.sh
./email_report.sh
```

`email_report.sh` will:
- Read `/var/log/delete_old_files.log` (or accept `deleted_count` and `deleted_bytes` as command‑line arguments).
- Compose a plain‑text email with subject **"Old file deletion report"** and a body that includes the count and bytes.
- Send the email using the `mail` command to the configured administrator address.

The script exits with status `0` on success; it returns a non‑zero code if the log file is missing, malformed, or if the mail command fails.

---

## Log Format

The deletion script produces a single line in `/var/log/delete_old_files.log` that follows this exact pattern:

```
deleted_count=<N> deleted_bytes=<B>
```

- `deleted_count` – Number of files that were removed.
- `deleted_bytes` – Total size (in bytes) of the removed files.

Both values are integers. The `email_report.sh` script parses this line to build the email body.

---

## Scheduling (Optional)

To run the cleanup automatically, add a cron entry (run as root or a user with appropriate permissions):

```cron
0 2 * * * /path/to/delete_old_files.sh && /path/to/email_report.sh
```

This example runs the deletion at 02:00 AM daily and, if successful, immediately sends the report.

---

## License

These scripts are provided **as‑is** under the MIT License. Feel free to adapt them to your environment.
