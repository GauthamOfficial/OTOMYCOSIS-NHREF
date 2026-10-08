"""Re-render the validation report from the saved evaluation.

The rendering itself lives in ear_detection.reporting. This only reads the
evaluation written by scripts/calibrate.py and writes the report, without
re-scoring the model.

Usage:
    python scripts/report.py
"""

import json
import os
import sys

from common import ARTIFACTS  # noqa: F401  (puts src/ on the path)
from ear_detection.reporting import (  # noqa: F401  (re-exported for callers)
    REPORT_HTML, REPORT_MD, markdown_to_html, render_html_page, render_report, write_reports,
)

EVALUATION = os.path.join(ARTIFACTS, "evaluation_report.json")


if __name__ == "__main__":
    if not os.path.isfile(EVALUATION):
        sys.exit("run scripts/calibrate.py first")
    with open(EVALUATION, encoding="utf-8") as fh:
        for path in write_reports(json.load(fh)):
            print("wrote", path)
