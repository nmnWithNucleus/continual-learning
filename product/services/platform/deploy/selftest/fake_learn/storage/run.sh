#!/usr/bin/env bash
# Fake storage service for the learn-loop platform self-test. Honours HOST/PORT.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_server.py" --role storage
