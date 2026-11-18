# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Exception handlers for Kiro Gateway.

Contains functions for handling validation errors and other exceptions
in a JSON-serialization compatible format.
"""

from typing import Any, List, Dict

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger


def sanitize_validation_errors(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converts validation errors to JSON-serializable format.
    
    Pydantic may include bytes objects in the 'input' field, which
    are not JSON-serializable. This function converts them to strings.
    
    Args:
        errors: List of validation errors from Pydantic
    
    Returns:
        List of errors with bytes converted to strings
    """
    sanitized = []
    for error in errors:
        sanitized_error = {}
        for key, value in error.items():
            if isinstance(value, bytes):
                # Convert bytes to string
                sanitized_error[key] = value.decode("utf-8", errors="replace")
            elif isinstance(value, (list, tuple)):
                # Recursively process lists
                sanitized_error[key] = [
                    v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for v in value
                ]
            else:
                sanitized_error[key] = value
        sanitized.append(sanitized_error)
    return sanitized


def _format_loc(loc) -> str:
    """
    Formats a Pydantic error location as a readable path, e.g. messages[100].content[1].

    The leading "body" and the union descriptors Pydantic inserts
    ("str", "list[union[...]]") are left out.
    """
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        elif part == "body" and not path:
            continue
        elif part == "str" or "[" in part:
            continue
        else:
            path += f".{part}" if path else part
    return path or "body"


def _union_member(loc: tuple):
    """
    Splits the location of an error inside a union of content block models.

    Pydantic reports such errors at (..., field, "list[union[...]]", index, Member, ...).
    Returns (block location, member name, rest of the location), or None when the
    error is not inside such a union.
    """
    for i in range(len(loc) - 2):
        if isinstance(loc[i], str) and loc[i].startswith("list[union[") and isinstance(loc[i + 1], int) and isinstance(loc[i + 2], str):
            return loc[:i + 2], loc[i + 2], loc[i + 3:]
    return None


def summarize_validation_errors(errors: List[Dict[str, Any]]) -> List[str]:
    """
    Turns Pydantic validation errors into one readable line per problem.

    A content block that fails validation makes Pydantic report errors for every
    union member, and for the alternative string form of the field. That noise is
    collapsed: a block whose type no member accepts becomes a single
    "Unsupported content block type" line; a block of a known type keeps only the
    errors of the member matching its type.

    Args:
        errors: Validation errors from Pydantic

    Returns:
        Readable error lines, in order of appearance
    """
    members: Dict[tuple, Dict[str, bool]] = {}  # block -> member -> type accepted
    block_types: Dict[tuple, Any] = {}
    for error in errors:
        split = _union_member(tuple(error.get("loc", ())))
        if split is None:
            continue
        block, member, rest = split
        type_rejected = rest == ("type",) and error.get("type") == "literal_error"
        accepted = members.setdefault(block, {})
        accepted[member] = accepted.get(member, True) and not type_rejected
        if type_rejected:
            block_types.setdefault(block, error.get("input"))

    # The field holding a failing block: its "str" alternative is union noise too
    fields = {block[:-2] for block in members}

    lines = []
    reported = set()
    for error in errors:
        loc = tuple(error.get("loc", ()))
        split = _union_member(loc)
        if split is None:
            if loc[:-1] in fields and loc[-1:] == ("str",):
                continue
            lines.append(f"{_format_loc(loc)}: {error.get('msg', 'invalid')}")
            continue
        block, member, rest = split
        if any(members[block].values()):
            if members[block][member]:
                lines.append(f"{_format_loc(block + rest)}: {error.get('msg', 'invalid')}")
        elif block not in reported:
            reported.add(block)
            lines.append(f"Unsupported content block type {block_types.get(block)!r} at {_format_loc(block)}")
    return lines


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Pydantic validation error handler.
    
    Logs error details and returns an informative response.
    Correctly handles bytes objects in errors by converting them to strings.
    Also flushes debug logs for validation errors when DEBUG_MODE is enabled.
    
    Args:
        request: FastAPI Request object
        exc: Validation exception from Pydantic
    
    Returns:
        JSONResponse with error details and status 422
    """
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace")
    
    # Sanitize errors for JSON serialization
    sanitized_errors = sanitize_validation_errors(exc.errors())
    
    summary = summarize_validation_errors(sanitized_errors)
    logger.error(f"Validation error (422): {'; '.join(summary) or sanitized_errors}")
    logger.debug(f"Validation error details: {sanitized_errors}")
    # Log body at DEBUG level to avoid cluttering console with potentially large payloads
    # logger.debug(f"Request body: {body_str[:500]}...")
    
    # Flush debug logs for validation errors
    # This is called AFTER middleware has initialized debug logging,
    # so all app logs during request processing will be captured
    try:
        from kiro.debug_logger import debug_logger
        if debug_logger:
            error_message = f"Validation error: {sanitized_errors}"
            debug_logger.flush_on_error(422, error_message)
    except ImportError:
        pass  # debug_logger not available
    
    return JSONResponse(
        status_code=422,
        content={"message": "; ".join(summary), "detail": sanitized_errors, "body": body_str[:500]},
    )