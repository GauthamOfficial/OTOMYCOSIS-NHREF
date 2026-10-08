"""Image preprocessing for the ear disease classifier.

Two strategies are available. ``load_image`` resizes the otoscope image
straight to the network input size. ``load_image_cropped`` first trims the
dark border that surrounds most otoscope captures, so the tympanic membrane
fills more of the frame before the resize.
"""

import numpy as np
from keras.preprocessing import image as keras_image

from .config import IMAGE_SIZE

# Pixels below this intensity are treated as part of the dark otoscope border.
BORDER_THRESHOLD = 100


def _to_batch(array):
    """Scale to [0, 1] and add the leading batch dimension."""
    array = np.array(array, dtype="float") / 255.0
    return np.expand_dims(array, axis=0)


def load_image(path):
    """Load an image and resize it to the model input size."""
    img = keras_image.load_img(path, target_size=(IMAGE_SIZE[0], IMAGE_SIZE[1], 3))
    return _to_batch(img)


def load_image_cropped(path):
    """Load an image, crop away the dark otoscope border, then resize.

    Falls back to :func:`load_image` when OpenCV is unavailable or the image
    is too dark for the crop to leave anything behind.
    """
    try:
        import cv2
    except ImportError:
        return load_image(path)

    img = cv2.imread(path)
    if img is None:
        return load_image(path)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = gray > BORDER_THRESHOLD
    if mask.any():
        img = img[np.ix_(mask.any(1), mask.any(0))]

    img = cv2.resize(img, IMAGE_SIZE)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return _to_batch(img)
