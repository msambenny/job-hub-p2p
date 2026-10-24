# Vendored code

`.claude/skills/job-alert-validation/` and `.claude/skills/resume-match/` are copied unchanged from
[msambenny/job_validation_skill](https://github.com/msambenny/job_validation_skill) at commit
`02f57bc9a613238c3f0c296e2f0d7b1df6b4eafe`.

The hub imports their scripts through `hub/reuse.py` and does not modify them, so the skills
also keep working on their own in Claude Code (`/job-alert-validation`, `/resume-match`).

To update, copy both folders again from the upstream repository, update the commit above, and run
`pytest` here and in `.claude/skills/job-alert-validation/`.
