"""Field of view normalisation. Runs without the model."""

import io
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from ear_detection import imaging  # noqa: E402

SIZE = imaging.INPUT_SIZE


def disk_image(width, height, radius_fraction=0.45, colour=(205, 130, 120)):
    """A lit circular field on black, like an otoscope capture."""
    yy, xx = np.mgrid[:height, :width]
    r = np.hypot(xx - width / 2, yy - height / 2)
    array = np.zeros((height, width, 3), np.uint8)
    array[r <= radius_fraction * min(width, height)] = colour
    return Image.fromarray(array)


def test_output_shape_and_type():
    out = imaging.normalize(disk_image(500, 500))
    assert out.shape == (SIZE, SIZE, 3)
    assert out.dtype == np.uint8


def test_everything_outside_the_circle_is_black():
    # A frame filling bright image is the hardest case: nothing is black to begin with.
    bright = Image.fromarray(np.full((480, 640, 3), 200, np.uint8))
    out = imaging.normalize(bright)
    assert out[~imaging.fov_mask()].max() == 0
    assert out[imaging.fov_mask()].min() > 0


def test_sources_become_indistinguishable_outside_the_circle():
    framed = imaging.normalize(disk_image(866, 866, radius_fraction=0.5))
    filling = imaging.normalize(Image.fromarray(np.full((500, 500, 3), 150, np.uint8)))
    outside = ~imaging.fov_mask()
    assert np.array_equal(framed[outside], filling[outside])


def test_letterboxed_capture_is_cropped_to_the_field():
    # A circle centred in a wide black frame should end up filling the output,
    # not shrunk inside it.
    out = imaging.normalize(disk_image(1200, 600, radius_fraction=0.48))
    centre_ring = out[SIZE // 2, int(SIZE * 0.08)]
    assert centre_ring.sum() > 0, "field of view was not expanded to the frame"


def test_dark_image_falls_back_to_whole_frame():
    out = imaging.normalize(Image.fromarray(np.full((300, 300, 3), 5, np.uint8)))
    assert out.shape == (SIZE, SIZE, 3)


def test_dihedral_views_are_eight_and_keep_the_mask():
    out = imaging.normalize(disk_image(500, 500))
    views = imaging.dihedral_views(out)
    assert views.shape == (8, SIZE, SIZE, 3)
    outside = ~imaging.fov_mask()
    for view in views:
        assert view[outside].max() == 0


def test_exif_orientation_is_applied():
    # A tall image stored sideways with an orientation tag must come back upright.
    img = Image.fromarray(np.zeros((100, 200, 3), np.uint8))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 degrees on display
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", exif=exif.tobytes())
    upright = imaging.open_image(buffer.getvalue())
    assert upright.size == (100, 200)


def test_input_statistics_ignore_the_mask_edge():
    flat = imaging.normalize(Image.fromarray(np.full((400, 400, 3), 120, np.uint8)))
    stats = imaging.input_statistics(flat)
    assert abs(stats["brightness"] - 120) < 1
    assert stats["sharpness"] < 1e-6, "the circle's own edge was counted as detail"
