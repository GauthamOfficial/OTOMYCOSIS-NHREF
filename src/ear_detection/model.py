"""Inference engine for the ear condition classifier.

The network is MobileNetV2 followed by global average pooling and one dense
layer. Because pooling and the dense layer are both linear, the model's logits
can be computed directly from the backbone's feature map, and the class
activation map (where in the image the evidence for a class came from) falls
out of the same arithmetic at no extra cost. This module therefore runs the
backbone itself and applies the head by hand, rather than calling the full
Keras model, so that a single forward pass yields both the prediction and the
explanation.
"""

import math
import threading

import numpy as np
import tensorflow as tf
from PIL import Image
from tensorflow import keras

from . import imaging


def softmax(logits):
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    return exp / exp.sum()


class Classifier:
    """Loads the trained model once and serves predictions from it."""

    def __init__(self, model_path, classes, temperature=1.0, use_tta=True):
        model = keras.models.load_model(model_path, compile=False)

        backbone = next((l for l in model.layers if isinstance(l, keras.Model)), None)
        if backbone is None:
            raise RuntimeError("Could not find the backbone inside %s" % model_path)
        dense = model.get_layer("predictions")

        self.backbone = backbone
        self.kernel, self.bias = [w.astype(np.float32) for w in dense.get_weights()]
        self.classes = list(classes)
        self.temperature = float(temperature)
        self.use_tta = bool(use_tta)

        if self.kernel.shape[1] != len(self.classes):
            raise RuntimeError(
                "Model has %d outputs but labels.json lists %d classes"
                % (self.kernel.shape[1], len(self.classes))
            )

        # Keras models are safe to call from several threads, but on a small
        # CPU machine serialising requests keeps memory predictable.
        self._lock = threading.Lock()

        # Warm up so the first real request is not slowed by graph tracing.
        self.features(np.zeros((1, imaging.INPUT_SIZE, imaging.INPUT_SIZE, 3), np.uint8))

    def features(self, views):
        """Backbone feature maps for a batch of normalised uint8 images."""
        batch = keras.applications.mobilenet_v2.preprocess_input(views.astype(np.float32))
        with self._lock:
            return self.backbone(tf.constant(batch), training=False).numpy()

    def logits_from_features(self, feats):
        """Apply the pooled linear head. Matches the Keras model exactly."""
        pooled = feats.mean(axis=(1, 2))
        return pooled @ self.kernel + self.bias

    def raw_logits(self, array, use_tta=None):
        """Uncalibrated logits for one normalised image, plus its feature map.

        With test time augmentation the logits are averaged over the eight
        rotations and reflections; the returned feature map is always the
        unrotated one, so the activation map lines up with the image shown.
        """
        tta = self.use_tta if use_tta is None else use_tta
        views = imaging.dihedral_views(array) if tta else array[None]
        feats = self.features(views)
        logits = self.logits_from_features(feats).mean(axis=0)
        return logits, feats[0]

    def predict(self, array):
        """Calibrated probabilities and the identity-view feature map."""
        logits, feats = self.raw_logits(array)
        return softmax(logits / self.temperature), feats

    def activation_map(self, feats, class_index):
        """Class activation map for one class, normalised to [0, 1]."""
        cam = feats @ self.kernel[:, class_index]
        cam = np.maximum(cam, 0.0)
        peak = cam.max()
        return cam / peak if peak > 0 else cam


def assess(probabilities):
    """Margin and normalised entropy of a probability vector.

    Returns the gap between the two most likely classes and the entropy as a
    fraction of its maximum, where 0 is a single certain class and 1 is a
    uniform guess.
    """
    ordered = np.sort(probabilities)[::-1]
    margin = float(ordered[0] - ordered[1]) if ordered.size > 1 else 1.0
    nonzero = probabilities[probabilities > 0]
    entropy = float(-(nonzero * np.log(nonzero)).sum())
    ceiling = math.log(probabilities.size) if probabilities.size > 1 else 1.0
    return margin, entropy / ceiling


# A sequential ramp from pale cyan to deep blue. Tympanic membranes and canals
# are pink, red and yellow, so a warm heat map vanishes into the anatomy; blue
# almost never occurs in an otoscope image and stays legible over it.
_HEAT_STOPS = np.array([
    [0.00, 190, 245, 255],
    [0.40, 60, 200, 240],
    [0.75, 25, 110, 225],
    [1.00, 20, 40, 170],
], dtype=np.float32)


def _heat_colour(values):
    """Map values in [0, 1] to RGB along the heat ramp."""
    positions = _HEAT_STOPS[:, 0]
    channels = [np.interp(values, positions, _HEAT_STOPS[:, c]) for c in (1, 2, 3)]
    return np.stack(channels, axis=-1)


def render_overlay(array, cam):
    """Blend an activation map over the normalised image.

    Opacity follows the activation, so regions the model ignored stay fully
    visible and only the regions it relied on are tinted.
    """
    size = array.shape[0]
    heat = Image.fromarray((cam * 255).astype(np.uint8), mode="L").resize(
        (size, size), Image.BICUBIC)
    heat = np.asarray(heat, dtype=np.float32) / 255.0
    heat[~imaging.fov_mask(size)] = 0.0

    alpha = (0.65 * heat)[..., None]
    blended = array.astype(np.float32) * (1 - alpha) + _heat_colour(heat) * alpha
    return np.clip(blended, 0, 255).astype(np.uint8)
