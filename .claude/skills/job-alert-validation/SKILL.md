---
name: job-alert-validation
description: Validate a WhatsApp-style job or career alert before sharing it — restructure the text into job fields, check the apply link is genuine, scrape the posting page, compare it with the alert, flag scam signals, and produce a copy-ready WhatsApp group message. Use when the user pastes a job alert, job description, internship, hiring drive or apply link, or asks whether a job posting is real.
argument-hint: "[job alert text or apply URL]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*) Bash(python ${CLAUDE_SKILL_DIR}/scripts/*)
---

# job-alert-validation

All scripts live in `${CLAUDE_SKILL_DIR}/scripts/` and work from any directory. Commands below use `python3`; use `python` on Windows.

Job alert from the user:

$ARGUMENTS

If nothing is shown above, ask the user to paste the job alert (text and apply link).

## Setup (first run only)

Check the dependencies, and install them if the import fails:

```bash
python3 -c "import bs4, requests, jsonschema, pypdf"
python3 -m pip install -r ${CLAUDE_SKILL_DIR}/requirements.txt
```

Job JSON shape is defined in [schemas/job_schema.json](schemas/job_schema.json).

## Instructions

1. Restructure the alert into these fields (leave a field as `""` or `[]` when the alert does not give it — never invent values):

- `company_name`, `job_title`, `department`
- `job_type` (Full-Time, Part-Time, Internship, …) and `work_mode` (On-site / Hybrid / Remote)
- `location`
- `salary_min`, `salary_max`, `benefits`
- `experience`, `education`, `skills` (list)
- `description` (responsibilities)
- `application_deadline`, `posted_date` (ISO `YYYY-MM-DD`; use today's date if the alert has none)
- `contact_name`, `contact_whatsapp`, `contact_email`
- `apply_link`, `custom_note`

Write it as JSON to a scratch file **outside** the skill folder (for example your scratchpad or a temp directory), such as `user_job.json`.

2. Run the full pipeline in one step:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/validate_job.py <path>/user_job.json --pretty --whatsapp --save
```

- Exit code `0` — comparison passed; the job is saved under `job_postings/` for resume matching and `whatsapp` holds the copy-ready message.
- Exit code `1` — mismatches; report `comparison.summary.critical_mismatches` and the mismatched fields. Nothing is saved.
- Exit code `2` — no URL in the JSON; ask the user for the apply link (or pass `--url`).
- Exit code `3` — URL validation failed. Read `url_validation` before telling the user anything:
  - `status_code` is set (404, 410, 500, …) → the link is broken. Tell the user, for example:
    > This job link appears invalid (HTTP status: 404). Do not apply through this posting until a working link is confirmed.
  - `status_code` is `null` and `error` mentions a proxy, tunnel, timeout or connection failure → **the site could not be reached from this machine**. That is not evidence the job is fake. Say the alert is *unverified*, do not save it, and suggest running the check on a machine with open internet (or allowing the host in the environment's network settings).

For offline checks against a saved page, pass `--scraped-json scraped_job.json` instead of hitting the network.

3. Review the `trust` object and surface every warning (domain mismatch, suspicious host such as Google Forms / Telegram / wa.me, duplicate posting). Also call out scam signals in the alert itself: registration or "refundable" fees, payment / UPI requests, unrealistic salary for the experience asked, personal email domains for a big employer. Legitimate paid tests (for example an official exam fee on the employer's own domain) are fine but worth one line.

4. If the posting is verified, show the WhatsApp message from `whatsapp` as copy-ready text in a code block. Do not send it anywhere. Layout: [references/whatsapp-template.md](references/whatsapp-template.md). If salary only produced a warning, mention it above the message, not inside it.

5. End with a one-line verdict: ✅ verified, ❌ do not share/apply (with the reason), or ⚠ unverified (could not reach the site).

### Running the steps individually

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/validate_url.py "<url>" --pretty
python3 ${CLAUDE_SKILL_DIR}/scripts/web_scraping.py "<url>" --pretty > scraped_job.json
python3 ${CLAUDE_SKILL_DIR}/scripts/compare_job.py user_job.json scraped_job.json --pretty
python3 ${CLAUDE_SKILL_DIR}/scripts/save_job.py user_job.json --pretty
python3 ${CLAUDE_SKILL_DIR}/scripts/format_whatsapp.py user_job.json
```

Only scrape after `validate_url.py` returns `"valid": true`. Prefer employer apply links (e.g. `on.tcs.com`, company careers pages) over LinkedIn URLs — LinkedIn often shows scrapers a login wall. Host rules live in [references/url_hosts.json](references/url_hosts.json). Comparison rules: [references/comparison-rules.md](references/comparison-rules.md) (`company_name`, `job_title` and `location` are critical; salary differences are warnings only).

## Job library (local index)

Saved jobs live in `${CLAUDE_SKILL_DIR}/job_postings/` (gitignored).

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/list_jobs.py --pretty
python3 ${CLAUDE_SKILL_DIR}/scripts/housekeep_jobs.py --pretty
python3 ${CLAUDE_SKILL_DIR}/scripts/housekeep_jobs.py --remove-stale --pretty
```

## Resume matching

When the user shares a resume, use the `resume-match` skill (`/resume-match <resume-path>`).

## Demo

```bash
python3 ${CLAUDE_SKILL_DIR}/demo/run_demo.py
```

Runs offline: a genuine alert, a suspicious alert, and resume matching. See [references/README.md](references/README.md) for the workflow diagram.
