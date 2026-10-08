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

    Returns the predicted label and the confidence of that prediction.
    """
    scores = model.predict(batch)
    index = int(np.argmax(scores))
    confidence = float(np.amax(scores))
    return CLASS_LABELS.get(index, "Unknown"), confidence
