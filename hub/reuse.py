"""Bridge to the vendored job_validation_skill scripts.

The validation, scraping, comparison, trust, resume scoring and WhatsApp
formatting logic lives unchanged in .claude/skills/job-alert-validation/scripts
(vendored from msambenny/job_validation_skill, see VENDORED.md). The hub imports
it from there instead of keeping a second copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / ".claude" / "skills" / "job-alert-validation"
SCRIPTS_DIR = SKILL_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from format_whatsapp import format_whatsapp_group_message  # noqa: E402
from job_library import (  # noqa: E402
    find_duplicate_postings,
    parse_posted_date,
    posting_freshness,
)
from match_resume import (  # noqa: E402
    MATCH_THRESHOLD,
    MAX_AGE_DAYS,
    normalize_filter,
    read_resume,
    score_resume_against_job,
)
from save_job import save_job_record, validate_job_record  # noqa: E402
from validate_job import run_job_validation  # noqa: E402

__all__ = [
    "MATCH_THRESHOLD",
    "MAX_AGE_DAYS",
    "SKILL_ROOT",
    "find_duplicate_postings",
    "format_whatsapp_group_message",
    "normalize_filter",
    "parse_posted_date",
    "posting_freshness",
    "read_resume",
    "run_job_validation",
    "save_job_record",
    "score_resume_against_job",
    "validate_job_record",
]
