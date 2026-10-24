# Comparison Rules

Used by `scripts/compare_job.py` in step 6 of the job-alert-validation workflow.

## Field matching

| Status | Meaning |
|--------|---------|
| `match` | User value aligns with scraped content (score >= 0.6) |
| `partial` | Some overlap but not a strong match (score 0.35–0.59) |
| `mismatch` | Values clearly differ (score < 0.35) |
| `missing_on_site` | User provided a value but the scraper found nothing |
| `skipped` | User did not provide this field |

## Matching logic

- Text fields are normalized (lowercase, collapsed whitespace) before comparison.
- A field matches if one value contains the other, or if fuzzy similarity is at least **0.6**.
- For `description`, the first 200 characters of the user text are also checked against the full scraped description.

## Skills

- Each user skill is checked for an exact or fuzzy match against scraped skills.
- At least **60%** of user skills must match for the skills field to pass.

## Critical fields

When the user provides these fields, a mismatch blocks approval:

- `company_name`
- `job_title`
- `location`

## Salary mismatches

If `salary_min` or `salary_max` does not match the website, treat it as a **warning** only. Tell the user the listed salary may not match the source. Do not reject the alert because of salary.

## Agent actions after comparison

**If `matches` is `true`:** Format a WhatsApp group message with `scripts/format_whatsapp.py` and show it as copy-ready text.

**If `matches` is `false` because of critical fields:** Show the mismatches and do not format the group message unless the user confirms.

**If `matches` is `false` only because of salary:** Warn the user, then format the group message.

**If the URL was invalid (step 4–5):** Do not scrape or compare. Warn the user not to apply through that link.
