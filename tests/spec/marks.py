"""The marker that makes the specifications executable.

``raises=NotImplementedError`` is the important part. It gives exactly the
behaviour a specification suite needs:

* stub still raising ``NotImplementedError`` -> XFAIL, suite green
* implemented and correct                    -> XPASS, suite green
* implemented and **wrong**                  -> FAILED, suite red

So a half-finished implementation cannot hide behind the marker: only the exact
"not written yet" exception is tolerated. Delete the marker as each function is
finished, and the test becomes an ordinary regression test.
"""

from __future__ import annotations

import pytest

unimplemented = pytest.mark.xfail(
    raises=NotImplementedError,
    strict=False,
    reason="core logic: implement by hand (see docs/SPEC.md)",
)
