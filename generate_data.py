#!/usr/bin/env python3
"""Generate the fixed synthetic source data once, before building insights."""
import argparse
from pathlib import Path

from utility_demo.config import load_config
from data_generation.data import generate, generate_neighborhood

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'output' / 'data'))
    args = parser.parse_args()
    config = load_config()
    target = Path(args.output).resolve()
    _, weather = generate(config, target)
    generate_neighborhood(config, weather, target)
    print(f'Synthetic source data ready: {target}')


if __name__ == '__main__':
    main()
