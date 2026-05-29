"""Shared platform-result dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PostResult:
    """Result of a post / reply / comment.

    On success, ``platform_post_id`` is whatever id the platform exposes
    (tweet id, LinkedIn post urn, Reddit comment fullname).

    If the post landed but URL/id extraction failed, ``success=True`` is
    still returned, ``warning`` is set, and ``platform_post_id`` is a
    synthetic placeholder.  We prefer this over ``success=False`` so the
    caller's queue doesn't repost a duplicate.
    """

    success: bool
    platform_post_id: str = ""
    url: str = ""
    text_preview: str = ""
    error: str = ""
    warning: str = ""
