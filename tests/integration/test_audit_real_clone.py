"""Real-network audit smoke test.

Skipped unless ``AITAP_RUN_INTEGRATION=1`` is set — we don't want CI to be
brittle to GitHub rate-limits or repo deletions, but it's worth having a
one-shot check that the gitpython integration actually works end-to-end.
"""

from __future__ import annotations

import os

import pytest

from aitap.audit.clone import audit_repo

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("AITAP_RUN_INTEGRATION") != "1",
    reason="requires AITAP_RUN_INTEGRATION=1 (real-network test)",
)
def test_audit_real_github_repo() -> None:
    # We pick a tiny, stable, classic repo that's unlikely to disappear.
    # `octocat/Hello-World` is GitHub's canonical demo repo.
    rc = audit_repo("gh:octocat/Hello-World")
    assert rc == 0
