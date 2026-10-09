"""统一异常与错误码（规范 4.7 / 9.3 / 10.4）。

异常分三类：InputError（用户/数据问题，报错即止）、RetryableError（LLM 超时/限流）、
PipelineError（阶段性失败，记录后继续其他章节）。禁止裸 except 吞错。
"""

from __future__ import annotations


class NoteGenError(Exception):
    """所有受控异常的基类，携带稳定错误码。"""

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str, *, code: str | None = None, detail: dict | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class InputError(NoteGenError):
    code = "INPUT_ERROR"
    http_status = 400


class RetryableError(NoteGenError):
    code = "RETRYABLE_ERROR"
    http_status = 502


class PipelineError(NoteGenError):
    code = "PIPELINE_ERROR"
    http_status = 500


class SchemaViolation(InputError):
    code = "SCHEMA_VIOLATION"
    http_status = 422


class AnchorNotFound(InputError):
    code = "ANCHOR_NOT_FOUND"
    http_status = 422


class ConflictError(NoteGenError):
    code = "CONFLICT"
    http_status = 409


class RevisionConflict(ConflictError):
    code = "REVISION_CONFLICT"
    http_status = 409


class DataCorrupt(NoteGenError):
    code = "DATA_CORRUPT"
    http_status = 500


class LockedError(NoteGenError):
    code = "LOCKED"
    http_status = 423


class BlockLocked(NoteGenError):
    code = "BLOCK_LOCKED"
    http_status = 403


class Forbidden(NoteGenError):
    code = "FORBIDDEN"
    http_status = 403


class ForbiddenOrigin(Forbidden):
    code = "FORBIDDEN_ORIGIN"
    http_status = 403


class RevisionGone(NoteGenError):
    code = "REVISION_GONE"
    http_status = 404


class NotFound(NoteGenError):
    code = "NOT_FOUND"
    http_status = 404


class PriceUnknown(NoteGenError):
    code = "PRICE_UNKNOWN"
    http_status = 422


def error_response(exc: NoteGenError) -> dict:
    return exc.to_dict()
