"""Field of view normalisation shared by training and inference.

The training data merges three source datasets that frame the ear differently.
One has a hard black circular mask, one fills the frame, one sits between.
Left alone, that framing identifies the source, and because most classes come
from a single source, the network can score well by recognising the dataset
rather than the ear. Measured before this module existed: 99 percent of one
source had pure black corners against 17 percent of another.

Every image therefore passes through the same steps before it reaches the
network, at training time and at inference time alike:

    1. locate the illuminated field of view and crop to it
    2. centre crop to a square
    3. resize to the network input size
    4. black out everything outside one fixed inscribed circle

After step 4 the region outside the circle is identical for every image from
every source, so it can no longer carry information about where an image came
from. This function is the single definition of that pipeline; training and
the service both import it, so the two cannot drift apart.
"""

import io

import numpy as np
from PIL import Image, ImageFilter, ImageOps

INPUT_SIZE = 224

# Radius of the retained circle as a fraction of the half width. Chosen from
# the measured radial luminance of each source: all three keep their content
# inside 0.95, and the harder edges of two of them sit between 0.95 and 1.0.
FOV_RADIUS = 0.95

# Luminance above which a pixel counts as part of the illuminated field when
# locating it. The darkest source keeps about 15 outside its circle.
FOV_THRESHOLD = 20

_MASK_CACHE = {}


def fov_mask(size=INPUT_SIZE, radius=FOV_RADIUS):
    """Boolean mask, True inside the retained circle."""
    key = (size, radius)
    if key not in _MASK_CACHE:
        yy, xx = np.mgrid[:size, :size]
        centre = (size - 1) / 2.0
        distance = np.hypot(xx - centre, yy - centre) / (size / 2.0)
        _MASK_CACHE[key] = distance <= radius
    return _MASK_CACHE[key]


def open_image(source):
    """Open a path, bytes or file object as an upright RGB image.

    Applies the EXIF orientation tag so phone captures are not sideways.
    """
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    image = Image.open(source)
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def _field_of_view_box(image):
    """Bounding box of the illuminated region, or the whole frame.

    Works on a reduced, lightly blurred copy so sensor noise and specular
    specks do not stretch the box.
    """
    width, height = image.size
    scale = min(1.0, 256.0 / max(width, height))
    small = image.convert("L").resize(
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        Image.BILINEAR,
    ).filter(ImageFilter.GaussianBlur(2))
    lit = np.asarray(small) > FOV_THRESHOLD

    # Too little lit area to trust the box: keep the whole frame.
    if lit.mean() < 0.3:
        return 0, 0, width, height

    rows = np.where(lit.mean(axis=1) > 0.02)[0]
    cols = np.where(lit.mean(axis=0) > 0.02)[0]
    if rows.size == 0 or cols.size == 0:
        return 0, 0, width, height

    top, bottom = rows[0] / scale, (rows[-1] + 1) / scale
    left, right = cols[0] / scale, (cols[-1] + 1) / scale
    return (
        max(0, int(left)), max(0, int(top)),
        min(width, int(round(right))), min(height, int(round(bottom))),
    )


def normalize(image):
    """Apply the full field of view pipeline.

    Returns a uint8 array of shape (INPUT_SIZE, INPUT_SIZE, 3) with everything
    outside the fixed circle set to zero.
    """
    image = image.crop(_field_of_view_box(image))

    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))

    image = image.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    array = np.asarray(image, dtype=np.uint8).copy()
    array[~fov_mask()] = 0
    return array


def input_statistics(array):
    """Brightness and sharpness inside the field of view.

    Used to flag uploads that fall outside the range the model was trained on.
    Sharpness is the variance of a discrete Laplacian, a standard focus measure.
    """
    grey = array.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    inside = fov_mask()

    laplacian = (
        -4.0 * grey[1:-1, 1:-1]
        + grey[:-2, 1:-1] + grey[2:, 1:-1]
        + grey[1:-1, :-2] + grey[1:-1, 2:]
    )
    # Only score pixels whose whole neighbourhood is inside the circle, so the
    # circle's own edge does not register as detail.
    core = inside[1:-1, 1:-1] & inside[:-2, 1:-1] & inside[2:, 1:-1] \
        & inside[1:-1, :-2] & inside[1:-1, 2:]

    return {
        "brightness": float(grey[inside].mean()),
        "sharpness": float(laplacian[core].var()),
    }


def dihedral_views(array):
    """The eight rotations and reflections of a square image.

    Otoscope orientation is arbitrary and either ear may be imaged, so all
    eight are equally valid presentations of the same view. Averaging the
    model over them removes any dependence on how the image happened to be
    held, and the fixed circular mask is unchanged by every one of them.
    """
    views = []
    for flipped in (array, array[:, ::-1]):
        for turns in range(4):
            views.append(np.rot90(flipped, k=turns))
    return np.stack([np.ascontiguousarray(v) for v in views])
