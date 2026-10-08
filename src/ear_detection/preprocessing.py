"""Image preprocessing for the ear condition classifier.

Whatever happens here must match what scripts/train.py did, byte for byte.
Training decodes the file, resizes to the network input size and applies
``mobilenet_v2.preprocess_input``, which maps pixels to [-1, 1]. Feeding the
network anything else, [0, 1] for instance, degrades it silently: the output
stays a valid probability vector, it is just wrong.
"""

import numpy as np
from keras.applications.mobilenet_v2 import preprocess_input
from keras.preprocessing import image as keras_image

from .config import IMAGE_SIZE

# Pixels below this intensity are treated as part of the dark otoscope border.
BORDER_THRESHOLD = 100


def _to_batch(array):
    """Apply the backbone's own scaling and add the batch dimension."""
    array = np.asarray(array, dtype="float32")
    return np.expand_dims(preprocess_input(array), axis=0)


def load_image(path):
    """Load an image and resize it to the model input size.

    This is the path the model was trained on and should be the default.
    """
    img = keras_image.load_img(path, target_size=(IMAGE_SIZE[0], IMAGE_SIZE[1], 3))
    return _to_batch(img)


def load_image_cropped(path):
    """Load an image, crop away the dark otoscope border, then resize.

    Note that the retrained model never saw cropped inputs: training resized
    whole frames. Cropping therefore shifts the input distribution away from
    what the network learned, and is offered only for comparison. Prefer
    :func:`load_image` unless a future training run also crops.

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

    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = grey > BORDER_THRESHOLD
    if mask.any():
        img = img[np.ix_(mask.any(1), mask.any(0))]

    img = cv2.resize(img, IMAGE_SIZE)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return _to_batch(img)
