"""Compatibility imports; deterministic analytics now live in data_factory.py."""
from .data_factory import agent_input, build_evidence, compare_neighborhood

__all__ = ['agent_input', 'build_evidence', 'compare_neighborhood']
