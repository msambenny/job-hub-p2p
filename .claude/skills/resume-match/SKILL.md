---
name: resume-match
description: Match a resume (PDF, text or Markdown) against the validated jobs saved by job-alert-validation, keeping only postings from the last 15 days. Use when the user uploads or points to a resume, or asks which saved jobs they can apply to.
argument-hint: "[resume-path] [--company X] [--location Y]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/../job-alert-validation/scripts/*) Bash(python ${CLAUDE_SKILL_DIR}/../job-alert-validation/scripts/*)
---

# resume-match

Companion to the `job-alert-validation` skill, which must sit next to this folder (it owns the script and the `job_postings/` library). Commands use `python3`; use `python` on Windows.

Arguments: $ARGUMENTS

If no resume path is given above, ask the user for it.

## Instructions

1. Run the matcher against the saved jobs:

```bash
python3 ${CLAUDE_SKILL_DIR}/../job-alert-validation/scripts/match_resume.py "<resume-path>" --pretty
python3 ${CLAUDE_SKILL_DIR}/../job-alert-validation/scripts/match_resume.py "<resume-path>" --company "Acme" --location "Bengaluru" --min-score 0.4 --limit 5 --pretty
```

Exit code `1` with `"match_count": 0` means no job fit; it is not an error. If the library is empty, tell the user to validate some alerts first with `/job-alert-validation`.

2. Keep a job only when **both** are true:
- The resume fits the job (skills, title, education, experience, description overlap).
- `posted_date` is present and the posting is **not older than 15 days**.

3. Skip a job when:
- `posted_date` is missing (cannot prove it is recent).
- The posting is older than 15 days.
- The resume does not match the job well enough.

4. Show matching jobs first (company, title, apply link, score, posting age). Then list skipped jobs with the skip reason.

5. If at least one job matches, share its details in WhatsApp format:

```bash
python3 ${CLAUDE_SKILL_DIR}/../job-alert-validation/scripts/format_whatsapp.py "<source_file from the match result>"
```

Do not invent a posting date. If the JSON has no `posted_date`, skip that job.
