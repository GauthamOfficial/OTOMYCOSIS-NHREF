"""Loading the trained model and turning its output into a reportable result."""

import math

import numpy as np
from keras import backend as K
from keras.models import load_model

from .config import (
    CLASS_LABELS,
    CONFIDENCE_MAX_ENTROPY_RATIO,
    CONFIDENCE_MIN_MARGIN,
    MODEL_PATH,
    PARENT_DISPLAY_NAMES,
    PARENT_GROUPS,
    RAW_CLASSES,
)


def load_classifier(model_path=None):
    """Load the trained classifier.

    The model is saved already compiled. It is not recompiled here because
    doing so with a different loss, as earlier versions did, silently discards
    the trained optimiser state and tells the caller nothing.
    """
    return load_model(model_path or MODEL_PATH, compile=False)


def clear_session():
    """Release the TensorFlow graph so the model can be loaded again."""
    K.clear_session()


def predict(model, batch):
    """Classify a preprocessed batch.

    Returns the top label, its probability, and the full distribution as
    ``(label, probability)`` pairs ordered most to least likely.
    """
    scores = model.predict(batch, verbose=0)[0]
    ranked = sorted(
        ((CLASS_LABELS.get(i, "Unknown"), float(p)) for i, p in enumerate(scores)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    label, confidence = ranked[0]
    return label, confidence, ranked


def group_totals(scores):
    """Roll the fine classes up into their coarse parents.

    The chronic subtypes are clinically one family, and the model can be sure
    a case is chronic while being unsure which subtype. Summing the members
    surfaces that, so a confident coarse answer is not hidden behind an
    uncertain fine one.
    """
    if not PARENT_GROUPS:
        return []

    by_name = {name: float(scores[i]) for i, name in enumerate(RAW_CLASSES)}
    totals = []
    for parent, members in PARENT_GROUPS.items():
        total = sum(by_name.get(m, 0.0) for m in members)
        totals.append({
            "label": PARENT_DISPLAY_NAMES.get(parent, parent),
            "probability": round(total, 4),
            "members": len(members),
        })
    return sorted(totals, key=lambda row: row["probability"], reverse=True)


def assess(ranked):
    """Judge whether a ranked distribution represents an actual decision.

    Returns the margin between the top two classes, entropy as a fraction of
    the maximum for this many classes, and whether the result is too flat to
    report as a finding.
    """
    probabilities = [value for _, value in ranked]

    margin = probabilities[0] - probabilities[1] if len(probabilities) > 1 else 1.0

    entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
    ceiling = math.log(len(probabilities)) if len(probabilities) > 1 else 1.0
    entropy_ratio = entropy / ceiling if ceiling else 0.0

    inconclusive = (
        margin < CONFIDENCE_MIN_MARGIN
        or entropy_ratio > CONFIDENCE_MAX_ENTROPY_RATIO
    )

    return {
        "margin": round(float(margin), 4),
        "entropy_ratio": round(float(entropy_ratio), 4),
        "inconclusive": bool(inconclusive),
    }
