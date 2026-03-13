"""Shared helpers for config and options flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .api import (
    EircSpbApiClient,
    EircSpbConfirmationError,
    EircSpbConnectionError,
    EircSpbError,
)
from .const import AUTH_TYPE_EMAIL, AUTH_TYPE_FLASHCALL, AUTH_TYPE_PHONE
from .const import CONF_CODE


@dataclass(slots=True)
class ChallengeState:
    """Mutable challenge state for multi-step flows."""

    transaction_id: str | None = None
    challenge_types: list[str] = field(default_factory=list)
    selected_challenge_type: str | None = None


def menu_options_for_challenges(challenge_types: list[str]) -> list[str]:
    """Return flow step ids for available challenge types."""
    mapping = {
        AUTH_TYPE_EMAIL: "email_confirmation",
        AUTH_TYPE_PHONE: "phone_confirmation",
        AUTH_TYPE_FLASHCALL: "flashcall_confirmation",
    }
    return [
        mapping[challenge_type]
        for challenge_type in challenge_types
        if challenge_type in mapping
    ]


def expected_confirmation_code_length(challenge_type: str | None) -> int:
    """Return the expected confirmation code length for the challenge type."""
    return 4 if challenge_type == AUTH_TYPE_FLASHCALL else 5


def confirmation_description_placeholders(
        challenge_type: str | None,
) -> dict[str, str]:
    """Build placeholders for the confirmation code step."""
    placeholders = {
        "code_length": str(expected_confirmation_code_length(challenge_type)),
        "flashcall_hint": "",
    }
    if challenge_type == AUTH_TYPE_FLASHCALL:
        placeholders["flashcall_hint"] = (
            "Last 4 digits of the phone number that called you."
        )
    return placeholders


async def async_confirm_code(
        build_client: Callable[[], EircSpbApiClient],
        transaction_id: str,
        challenge_type: str,
        code: str,
) -> tuple[EircSpbApiClient, dict[str, Any]]:
    """Confirm a challenge code with a freshly built client."""
    client = build_client()
    payload = await client.async_confirm_challenge(transaction_id, challenge_type, code)
    return client, payload


async def async_send_confirmation(
        build_client: Callable[[], EircSpbApiClient],
        transaction_id: str,
        challenge_type: str,
) -> None:
    """Send a confirmation challenge with a freshly built client."""
    client = build_client()
    await client.async_send_confirmation(transaction_id, challenge_type)


async def async_send_confirmation_with_errors(
        build_client: Callable[[], EircSpbApiClient],
        transaction_id: str,
        challenge_type: str,
) -> str | None:
    """Send a confirmation and map API exceptions to flow error keys."""
    try:
        await async_send_confirmation(build_client, transaction_id, challenge_type)
    except EircSpbConnectionError:
        return "cannot_connect"
    except EircSpbConfirmationError:
        return "confirmation_failed"
    return None


async def async_validate_confirmation_input(
        build_client: Callable[[], EircSpbApiClient],
        transaction_id: str,
        challenge_type: str,
        user_input: dict[str, str] | None,
) -> tuple[dict[str, str], tuple[EircSpbApiClient, dict[str, Any]] | None]:
    """Validate confirmation input and submit it when valid."""
    errors: dict[str, str] = {}
    if user_input is None:
        return errors, None

    code = user_input[CONF_CODE].strip()
    expected_length = expected_confirmation_code_length(challenge_type)
    if len(code) != expected_length:
        errors[CONF_CODE] = "invalid_code_length"
        return errors, None

    try:
        return errors, await async_confirm_code(
            build_client,
            transaction_id,
            challenge_type,
            code,
        )
    except EircSpbConfirmationError:
        errors["base"] = "invalid_confirmation_code"
    except EircSpbConnectionError:
        errors["base"] = "cannot_connect"
    except EircSpbError:
        errors["base"] = "unknown"

    return errors, None
