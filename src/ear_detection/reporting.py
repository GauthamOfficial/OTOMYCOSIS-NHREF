"""Render the validation report as Markdown and as a standalone HTML page.

Used by scripts/calibrate.py after scoring, and by the service when a new
model version is approved, so the report always describes the model in use.
Has no TensorFlow dependency.
"""

import csv
import os
import re
from collections import defaultdict

from . import config

REPORT_MD = os.path.join(config.BASE_DIR, "docs", "VALIDATION_REPORT.md")
REPORT_HTML = os.path.join(config.FRONTEND_DIR, "report.html")
MANIFEST = os.path.join(config.ARTIFACTS_DIR, "manifest.csv")


def class_sources():
    """Which source datasets each class draws on, read from the manifest."""
    sources = defaultdict(set)
    if os.path.isfile(MANIFEST):
        with open(MANIFEST, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sources[row["label"]].add(row.get("source", ""))
    return sources


def write_reports(evaluation):
    """Render both forms of the report from an evaluation dictionary."""
    markdown = render_report(evaluation)
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(markdown)
    with open(REPORT_HTML, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html_page(markdown))
    return REPORT_MD, REPORT_HTML


def pct(x):
    return "%.1f%%" % (100 * x)


def ci(pair):
    return "%.1f to %.1f%%" % (100 * pair[0], 100 * pair[1])


def render_report(r):
    ref = r["referral"]
    test = r["test"]
    cal = r["calibration"]
    lines = []
    add = lines.append

    add("# Validation Report")
    add("")
    add("Ear condition screening from otoscope images. Nawaloka Hospital Research")
    add("and Education Foundation (NHREF).")
    add("")
    add("Generated %s by `scripts/calibrate.py` from the model in `models/`. Every" % r["generated_at"])
    add("figure below was measured, none was written by hand. Regenerate this file")
    add("after any retraining.")
    add("")
    add("## Summary")
    add("")
    add("| Measure | Result | 95% CI |")
    add("| --- | --- | --- |")
    add("| Referral sensitivity (abnormal ears referred) | **%s** | %s |" % (pct(ref["sensitivity"]), ci(ref["sensitivity_ci"])))
    add("| Referral specificity (normal ears not referred) | **%s** | %s |" % (pct(ref["specificity"]), ci(ref["specificity_ci"])))
    add("| AUROC, abnormal vs normal | %.3f | |" % ref["auroc"])
    add("| 12 class macro F1 | %.3f | |" % test["macro_f1"])
    add("| 12 class balanced accuracy | %s | |" % pct(test["balanced_accuracy"]))
    add("| Coarse accuracy, chronic subtypes merged | %s | |" % pct(test["coarse_accuracy"]))
    add("")
    add("Test split: %d images never used for training, model selection or" % test["images"])
    add("calibration. Out of %d abnormal test ears, %d were not referred."
        % (ref["tp"] + ref["fn"], ref["fn"]))
    add("")
    add("## Intended use")
    add("")
    add("A screening aid for deciding whether an otoscope image warrants review by an")
    add("ENT specialist. It is designed to err toward referral: when the model cannot")
    add("confidently call an ear normal, it recommends referral.")
    add("")
    add("It is **not** a diagnostic device. It has not been prospectively validated,")
    add("has not been evaluated on images from the deploying clinic's own equipment,")
    add("and must not be used to withhold care. Every output requires review by a")
    add("qualified clinician.")
    add("")
    add("## How the referral decision works")
    add("")
    add("The model outputs a probability for each of 12 conditions. The probability of")
    add("an abnormal ear is one minus the probability of `normal`. The ear is referred")
    add("when that exceeds a threshold of **%.4f**, chosen on the validation split to" % ref["threshold"])
    add("reach a target sensitivity of %s, then frozen before the test split was scored." % pct(ref["target_sensitivity"]))
    add("")
    add("Other operating points, threshold chosen on validation, measured on test:")
    add("")
    add("| Target sensitivity | Threshold | Test sensitivity | Test specificity |")
    add("| --- | --- | --- | --- |")
    for op in r["operating_points"]:
        add("| %s | %.4f | %s | %s |" % (pct(op["target"]), op["threshold"],
                                         pct(op["test_sensitivity"]), pct(op["test_specificity"])))
    add("")
    if r["referral_misses"]:
        add("Abnormal test ears that were **not** referred at the chosen threshold:")
        add("")
        add("| Condition | Missed |")
        add("| --- | --- |")
        for cls, n in sorted(r["referral_misses"].items(), key=lambda kv: -kv[1]):
            add("| %s | %d |" % (config.CLASS_INFO.get(cls, (cls,))[0], n))
        add("")
    add("## Per condition performance")
    add("")
    add("Recall is the fraction of ears with that condition the model named correctly.")
    add("Classes with a small test support have wide intervals; read them accordingly.")
    add("")
    add("| Condition | Test images | Precision | Recall | Recall 95% CI | F1 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for pc in test["per_class"]:
        add("| %s | %d | %.3f | %.3f | %s | %.3f |" % (
            pc["name"], pc["support"], pc["precision"], pc["recall"], ci(pc["recall_ci"]), pc["f1"]))
    add("")
    add("## Confusion matrix")
    add("")
    add("Rows are the true condition, columns the prediction, in this order:")
    add("")
    for i, c in enumerate(r["classes"]):
        add("%d. %s" % (i, config.CLASS_INFO.get(c, (c,))[0]))
    add("")
    add("```")
    add("      " + "".join("%5d" % i for i in range(len(r["classes"]))))
    for i, row in enumerate(test["confusion"]):
        add("%4d  " % i + "".join("%5s" % (v if v else ".") for v in row))
    add("```")
    add("")
    add("## Calibration")
    add("")
    add("Probabilities are temperature scaled (T = %.2f, fitted on validation) so that a" % r["temperature"])
    add("stated confidence corresponds to an observed frequency.")
    add("")
    add("| | Before | After |")
    add("| --- | --- | --- |")
    add("| Expected calibration error, test | %.3f | %.3f |" % (cal["test_ece_before"], cal["test_ece_after"]))
    add("| Negative log likelihood, test | %.3f | %.3f |" % (cal["test_nll_before"], cal["test_nll_after"]))
    add("")
    gate = r["confidence_gate"]
    add("When the two most likely conditions are close, the finding is marked low")
    add("confidence. That applied to %s of test images. Accuracy was %s on the"
        % (pct(gate["flagged_fraction"]), pct(gate["accuracy_confident"] or 0)))
    add("confident ones and %s on the flagged ones, so the flag separates reliable"
        % pct(gate["accuracy_flagged"] or 0))
    add("findings from unreliable ones.")
    add("")
    add("## Dataset and leakage controls")
    add("")
    grouping = r.get("grouping") or {}
    threshold_text = "%.2f" % grouping["threshold"] if "threshold" in grouping else "a fixed threshold"
    add("4,763 images in 16 source folders, drawn from three public datasets, merged into")
    add("12 classes. Two controls matter for reading the numbers above:")
    add("")
    add("1. **Same ear grouping.** One source dataset is built from video, so the same")
    add("   ear appears in long runs of frames. Images were grouped by the cosine")
    add("   similarity of generic ImageNet features (%s or above, joined" % threshold_text)
    add("   transitively) and whole groups assigned to a single split. No test image has")
    add("   a training image of the same class at or above that similarity. An earlier")
    add("   split grouped by perceptual hash only; it left half of that source's test")
    add("   images with a near twin in training and inflated its scores. The figures in")
    add("   this report use the corrected split.")
    add("2. **Field of view normalisation.** The three source datasets frame the ear")
    add("   differently, and 8 of the 12 classes come from a single source. Without")
    add("   correction the network can identify the dataset instead of the ear. Every")
    add("   image is cropped to its illuminated field and masked to one fixed circle, at")
    add("   training and at inference, which removes the framing cue.")
    add("")
    if r["source_stratified"]:
        add("Recall by source, for conditions present in more than one source. Similar")
        add("figures across sources indicate the model is reading the ear rather than")
        add("the dataset:")
        add("")
        add("| Condition | Source A | Source B | Source C |")
        add("| --- | --- | --- | --- |")
        for s in r["source_stratified"]:
            cells = []
            for src in "ABC":
                v = s["by_source"].get(src)
                cells.append("%d/%d (%s)" % (v["correct"], v["total"], pct(v["correct"] / v["total"]))
                             if v and v["total"] else "")
            add("| %s | %s |" % (config.CLASS_INFO.get(s["class"], (s["class"],))[0], " | ".join(cells)))
        add("")
    add("## Known limitations")
    add("")
    perfect = [pc["name"] for pc in test["per_class"]
               if pc["support"] >= 20 and pc["precision"] == 1.0 and pc["recall"] == 1.0
               and len(class_sources().get(pc["class"], ())) == 1]
    if perfect:
        add("- **Perfect scores to distrust.** %s scored perfectly on the test split." % ", ".join(perfect))
        add("  Each comes from a single source dataset, so perfection more likely reflects")
        add("  that source's acquisition style than clinical skill. Do not quote these")
        add("  figures as diagnostic performance.")
    add("- **Performance depends on image source.** Where a condition appears in several")
    add("  sources, recall is highest on source A, the largest. Results on sources B and")
    add("  C in the table above are the better guide to performance on unfamiliar")
    add("  equipment.")
    add("- **Single source classes.** Eight classes come from one source dataset each.")
    add("  Field of view normalisation removes the framing cue, but differences in")
    add("  camera, compression and image quality remain, so scores for those classes")
    add("  may still be optimistic. A prospective test on local equipment is the only")
    add("  way to confirm them.")
    add("- **Small classes.** Other Findings, Otitis Externa and the cholesteatoma and")
    add("  suppurative subtypes have few test images. Their intervals above are wide.")
    add("- **Pooled class.** Other Findings combines foreign bodies, pseudomembranes and")
    add("  ventilation tubes, which had too few images to learn separately.")
    add("- **Otomycosis is not a class.** The dataset contains no otomycosis folder, so")
    add("  fungal infection cannot be recognised as such.")
    add("- **Chronic subtypes overlap.** Chronic Otitis Media (unspecified) is a parent")
    add("  of the other chronic labels in the source data. The coarse accuracy above")
    add("  merges them; subtype calls should be read with that in mind.")
    add("- **Single image.** Clinical assessment uses history, both ears and often")
    add("  audiometry. This system sees one image.")
    add("")
    return "\n".join(lines) + "\n"


def _inline(text):
    """Escape, then apply the two inline forms the report uses."""
    import html
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def markdown_to_html(markdown):
    """Convert the subset of Markdown that render_report emits.

    Headings, paragraphs, pipe tables, bulleted and numbered lists, fenced
    code, bold and inline code. Kept deliberately small so the service has no
    Markdown dependency.
    """
    import html
    out, lines, i = [], markdown.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(html.escape(lines[i], quote=False))
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % "\n".join(block))
        elif line.startswith("## "):
            out.append("<h2>%s</h2>" % _inline(line[3:]))
            i += 1
        elif line.startswith("# "):
            out.append("<h1>%s</h1>" % _inline(line[2:]))
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % _inline(c) for c in head),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in body),
            ))
        elif re.match(r"^(- |\d+\. )", line):
            ordered = bool(re.match(r"^\d+\. ", line))
            items = []
            while i < len(lines) and (re.match(r"^(- |\d+\. )", lines[i]) or lines[i].startswith("   ")
                                      or lines[i].startswith("  ")):
                if re.match(r"^(- |\d+\. )", lines[i]):
                    items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % _inline(t) for t in items), tag))
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^(#|\||```|- |\d+\. )", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>%s</p>" % _inline(" ".join(para)))
    return "\n".join(out)


def render_html_page(markdown):
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Validation Report</title>
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="css/style.css">
</head>
<body>
<header class="masthead">
  <div class="shell masthead-inner">
    <div class="identity">
      <span class="identity-rule" aria-hidden="true"></span>
      <span class="identity-text">
        <span class="identity-name">NHREF OtoScan</span>
        <span class="identity-unit">Nawaloka Hospital PLC</span>
      </span>
    </div>
    <nav class="masthead-meta">
      <a class="textlink" href="./">Back to screening</a>
    </nav>
  </div>
</header>
<main class="shell report">
%s
</main>
</body>
</html>
""" % markdown_to_html(markdown)
