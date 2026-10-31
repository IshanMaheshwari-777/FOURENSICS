"""Real Docker runs. Skipped unless RUN_DOCKER_TESTS=1 (needs Docker and network for the image and PyPI)."""
from __future__ import annotations

import os

import pytest

from app.pipeline.sandbox import reproduce
from app.schemas import Anchor, Claim

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="set RUN_DOCKER_TESTS=1")

ENTRY = {"url": "x", "package": "six", "version": "1.16.0", "python": "3.11"}
CLAIM = Claim(id="C1", text="json.loads fails on '{'", type="behavior", is_primary=True, source_span="x")


def test_reproduced():
    anchors = [Anchor(kind="code_block", value='import json\njson.loads("{")'),
               Anchor(kind="error", value="JSONDecodeError: Expecting property name")]
    r = reproduce(ENTRY, CLAIM, anchors)
    assert r.status == "reproduced", r


def test_not_reproduced_and_no_network():
    anchors = [Anchor(kind="code_block", value=(
        "import socket\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    print('NETWORK')\n"
        "except OSError:\n    print('no network')\n")),
        Anchor(kind="error", value="JSONDecodeError")]
    r = reproduce(ENTRY, CLAIM, anchors)
    assert r.status == "not_reproduced", r
    assert "NETWORK" not in r.raw and "no network" in r.raw
