"""Nucleus Output Service (serve-loop MVP v0.0).

Two deliverables live here:

1. ``static/c9_reader.js`` — the browser-side C9 reader/renderer (the actual
   delivery to the computer surface). This Python package does NOT run it; the
   modules below are faithful mirrors of its parsing + markdown logic so the
   behaviour can be tested with pytest in an environment without a browser.
2. ``main.py`` — a thin standalone relay service (:8082) that proxies a C9
   stream from an upstream URL to the caller unchanged, with a delivery ack.
"""
