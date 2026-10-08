"""Turn model output into the result a clinician sees.

``build_result`` works from stored probabilities alone, so an examination
saved months ago renders through exactly the same code as a live one.
"""

import math

import numpy as np

from . import config, imaging
from .model import assess, render_overlay


def wilson(successes, total, z=1.96):
    """Wilson score interval for a proportion; (None, None) when total is 0."""
    if not total:
        return (None, None)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def build_result(probabilities, classes, parent_groups, threshold):
    """Referral, finding, differential and chronic rollup.

    ``probabilities`` is either an array in class order or a mapping from
    class name to probability.
    """
    if isinstance(probabilities, dict):
        probs = np.array([float(probabilities.get(c, 0.0)) for c in classes])
    else:
        probs = np.asarray(probabilities, dtype=float)

    order = np.argsort(probs)[::-1]
    top = int(order[0])
    margin, entropy_ratio = assess(probs)
    low_confidence = (
        margin < config.CONFIDENCE_MIN_MARGIN
        or entropy_ratio > config.CONFIDENCE_MAX_ENTROPY_RATIO
    )

    normal_index = classes.index(config.NORMAL_CLASS)
    abnormal = float(1.0 - probs[normal_index])
    refer = abnormal >= threshold

    by_name = {c: float(probs[i]) for i, c in enumerate(classes)}
    groups = [
        {
            "name": config.PARENT_DISPLAY_NAMES.get(parent, parent),
            "members": members,
            "probability": round(sum(by_name.get(m, 0.0) for m in members), 4),
        }
        for parent, members in parent_groups.items()
    ]

    return {
        "referral": {
            "recommendation": "refer" if refer else "routine",
            "abnormal_probability": round(abnormal, 4),
            "threshold": round(float(threshold), 4),
        },
        "finding": {
            "class": classes[top],
            "name": config.display_name(classes[top]),
            "description": config.description(classes[top]),
            "probability": round(float(probs[top]), 4),
            "low_confidence": bool(low_confidence),
            "margin": round(margin, 4),
            "entropy_ratio": round(entropy_ratio, 4),
        },
        # Most likely first, so the prediction always heads the list.
        "differential": [
            {
                "class": classes[i],
                "name": config.display_name(classes[i]),
                "probability": round(float(probs[i]), 4),
            }
            for i in order
        ],
        "groups": groups,
    }


def quality_warnings(stats, ranges):
    """Flag inputs outside the range the model was trained on."""
    warnings = []
    brightness = (ranges or {}).get("brightness", {})
    sharpness = (ranges or {}).get("sharpness", {})
    if brightness and stats["brightness"] < brightness["p01"]:
        warnings.append("Image is darker than 99% of training images. Results may be unreliable.")
    if brightness and stats["brightness"] > brightness["p99"]:
        warnings.append("Image is brighter than 99% of training images. Results may be unreliable.")
    if sharpness and stats["sharpness"] < sharpness["p01"]:
        warnings.append("Image is blurrier than 99% of training images. Results may be unreliable.")
    return warnings


def analyse(classifier, meta, image):
    """Run the full pipeline on an opened image.

    Returns the result for the client and the arrays and numbers that are
    persisted with the examination.
    """
    classes = meta["classes"]
    array = imaging.normalize(image)
    probabilities, feats = classifier.predict(array)

    result = build_result(probabilities, classes, meta["parent_groups"],
                          meta["referral_threshold"])
    top = classes.index(result["finding"]["class"])
    overlay = render_overlay(array, classifier.activation_map(feats, top))

    stats = imaging.input_statistics(array)
    warnings = quality_warnings(stats, meta["input_stats"])
    result["quality"] = {
        "warnings": warnings,
        "brightness": round(stats["brightness"], 1),
        "sharpness": round(stats["sharpness"], 1),
    }

    extras = {
        "model_view": array,
        "overlay": overlay,
        "probabilities": {c: round(float(p), 6) for c, p in zip(classes, probabilities)},
        "warnings": warnings,
    }
    return result, extras
