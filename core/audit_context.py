from contextlib import contextmanager
from contextvars import ContextVar
import uuid


_audit_context = ContextVar("dvsolutions_audit_context", default=None)


def set_audit_request(request):
    return _audit_context.set({
        "request": request,
        "request_id": uuid.uuid4(),
        "reason": "",
        "user": None,
    })


def reset_audit_request(token):
    _audit_context.reset(token)


def get_audit_context():
    return _audit_context.get() or {}


@contextmanager
def audit_scope(*, user=None, reason="", request_id=None):
    token = _audit_context.set({
        "request": None,
        "request_id": request_id or uuid.uuid4(),
        "reason": (reason or "").strip(),
        "user": user,
    })
    try:
        yield
    finally:
        _audit_context.reset(token)
