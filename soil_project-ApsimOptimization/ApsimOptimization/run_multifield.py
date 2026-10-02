"""Standalone multi-field entry point; legacy calibration commands remain available."""
from src.multifield_optimizer import main

if __name__ == '__main__':
    raise SystemExit(main())
