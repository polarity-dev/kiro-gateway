# -*- coding: utf-8 -*-

"""Factories for building Kiro authentication from environment settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from kiro.auth import KiroAuthManager
from kiro.dotenv_utils import read_raw_dotenv_value


class AuthConfigurationError(RuntimeError):
    """Raised when no supported Kiro credential source is configured."""


def build_auth_manager_from_environment(
    env_file: Optional[Path] = None,
) -> KiroAuthManager:
    """Build an auth manager from the gateway's environment configuration.

    Args:
        env_file: Optional dotenv file to load before reading environment values.
            Values from this file override inherited values so setup can consume a
            file it has just written.

    Returns:
        Configured Kiro authentication manager.

    Raises:
        AuthConfigurationError: If no supported credential source is configured.
    """
    values = dict(os.environ)
    if env_file is not None:
        values.update(
            key_value
            for key_value in dotenv_values(env_file).items()
            if key_value[1] is not None
        )
        for variable in ("KIRO_CREDS_FILE", "KIRO_CLI_DB_FILE"):
            raw_value = read_raw_dotenv_value(env_file, variable)
            if raw_value is not None:
                values[variable] = raw_value

    region = values.get("KIRO_REGION", "us-east-1")
    api_region = values.get("KIRO_API_REGION") or None
    profile_arn = values.get("PROFILE_ARN") or None
    sqlite_db = values.get("KIRO_CLI_DB_FILE")
    creds_file = values.get("KIRO_CREDS_FILE")
    refresh_token = values.get("REFRESH_TOKEN")

    common = {
        "profile_arn": profile_arn,
        "region": region,
        "api_region": api_region,
    }
    if sqlite_db:
        return KiroAuthManager(sqlite_db=str(Path(sqlite_db).expanduser()), **common)
    if creds_file:
        return KiroAuthManager(creds_file=str(Path(creds_file).expanduser()), **common)
    if refresh_token:
        return KiroAuthManager(refresh_token=refresh_token, **common)

    raise AuthConfigurationError(
        "No Kiro credentials found. Set KIRO_CLI_DB_FILE, KIRO_CREDS_FILE, "
        "or REFRESH_TOKEN in .env."
    )
