"""Entry point for the ear condition screening service.

Nawaloka Hospital Research and Education Foundation (NHREF).

    python app.py                 serve on http://localhost:5000
    python app.py --port 8080     choose another port
    python app.py --open          also open the interface in a browser

Serves with Waitress, a production WSGI server that runs on Windows, and falls
back to Flask's development server only if Waitress is not installed. The
module level ``app`` is what uWSGI loads on Linux (see uwsgi.ini).
"""

import argparse
import logging
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

# Quieten TensorFlow's start up chatter before it is imported.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# TensorFlow logs API deprecation notices at load time that mean nothing to
# an operator and look like faults.
logging.getLogger("tensorflow").setLevel(logging.ERROR)

from ear_detection import create_app  # noqa: E402

app = create_app()


def main():
    parser = argparse.ArgumentParser(description="Ear condition screening service")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="interface to bind; use 0.0.0.0 to allow other machines")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--open", action="store_true", help="open the interface in a browser")
    args = parser.parse_args()

    url = "http://%s:%d" % ("localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host,
                            args.port)
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    try:
        from waitress import serve
    except ImportError:
        logging.warning("waitress not installed, using the Flask development server")
        app.run(host=args.host, port=args.port, debug=False)
        return

    logging.info("serving on %s", url)
    serve(app, host=args.host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
