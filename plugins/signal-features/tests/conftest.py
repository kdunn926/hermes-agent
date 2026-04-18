# Tests should be run with --import-mode=importlib to avoid importing the
# parent plugin __init__.py (which has relative imports requiring the
# full hermes gateway).
#
# Example:
#   pytest plugins/signal-features/tests/ --import-mode=importlib -o "addopts="
