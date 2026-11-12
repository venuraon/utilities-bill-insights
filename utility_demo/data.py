"""Preserve the original demo import path for synthetic data generation.

The implementations live in :mod:`data_generation.data`. Existing callers can
continue importing them here; new generator code can import the source module.
"""
from data_generation.data import generate, generate_neighborhood, write_csv
