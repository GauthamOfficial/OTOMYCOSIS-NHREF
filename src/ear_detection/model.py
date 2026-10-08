"""Loading the trained Keras model and turning its output into a label."""

import numpy as np
from keras import backend as K
from keras.models import load_model

from .config import CLASS_LABELS, MODEL_PATH


def load_classifier(model_path=None):
    """Load and compile the trained classifier."""
    model = load_model(model_path or MODEL_PATH)
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def clear_session():
    """Release the TensorFlow graph so the model can be loaded again."""
    K.clear_session()


def predict(model, batch):
    """Classify a preprocessed batch.

    Returns the predicted label, the confidence of that prediction, and the
    full distribution as a list of ``(label, probability)`` pairs ordered from
    most to least likely. The distribution is what lets the interface show a
    differential rather than a single answer.
    """
    scores = model.predict(batch)[0]
    ranked = sorted(
        ((CLASS_LABELS.get(i, "Unknown"), float(p)) for i, p in enumerate(scores)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    label, confidence = ranked[0]
    return label, confidence, ranked
