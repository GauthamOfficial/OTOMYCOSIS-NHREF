"""NHREF OtoScan: ear condition screening from otoscope images.

Nawaloka Hospital Research and Education Foundation (NHREF).
"""

__version__ = "2.0.0"


def create_app():
    """Build the Flask application.

    Imported lazily so that training scripts can use ``ear_detection.imaging``
    without pulling in the web layer or loading the model.
    """
    from .api import create_app as _create_app

    return _create_app()


__all__ = ["create_app", "__version__"]
