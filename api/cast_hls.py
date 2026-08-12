"""Compatibility facade for the device-neutral HLS relay.

New code should import :mod:`media.hls`.  This module remains so existing tests
and external imports do not break during the refactor.
"""
from media.hls import *  # noqa: F401,F403
