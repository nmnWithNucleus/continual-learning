#!/usr/bin/env bash
# Fake data-processing service for the learn-loop self-test. Honours HOST/PORT.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_server.py" --role data-processing
