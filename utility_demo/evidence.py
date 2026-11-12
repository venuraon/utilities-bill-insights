"""Preserve the original demo import path for evidence helpers.

The deterministic analytics implementations live in
:mod:`utility_demo.data_factory` and are re-exported here for existing callers.
"""
from .data_factory import agent_input, build_evidence, compare_neighborhood

__all__ = ['agent_input', 'build_evidence', 'compare_neighborhood']
