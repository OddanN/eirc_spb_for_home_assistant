"""Version: 0.0.1. API client for EIRC SPB."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_ACCOUNT_GROUPS_PATH,
    API_ACCOUNT_DETAILS_PATH,
    API_AUTH_PATH,
    API_BASE_URL,
    API_CURRENT_USER_PATH,
    API_CUSTOMER,
)


class EircSpbError(Exception):
    """Base exception for the integration."""


class EircSpbAuthError(EircSpbError):
    """Raised when authentication fails."""


class EircSpbConnectionError(EircSpbError):
    """Raised when the API is unavailable."""


class EircSpbConfirmationRequired(EircSpbError):
    """Raised when additional confirmation is required."""

    def __init__(self, transaction_id: str, types: list[str]) -> None:
        """Initialize the exception."""
        super().__init__("Additional confirmation required")
        self.transaction_id = transaction_id
        self.types = types


class EircSpbConfirmationError(EircSpbError):
    """Raised when sending or validating confirmation fails."""


class EircSpbReauthRequired(EircSpbAuthError):
    """Raised when reauthentication requires confirmation."""

    def __init__(self, transaction_id: str, types: list[str]) -> None:
        """Initialize the exception."""
        super().__init__("Reauthentication requires confirmation")
        self.transaction_id = transaction_id
        self.types = types


class EircSpbApiClient:
    """Thin API client for EIRC SPB."""

    def __init__(
            self,
            hass: HomeAssistant,
            auth_type: str,
            login: str,
            password: str,
            auth_payload: dict[str, Any] | None = None,
            session_cookie: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = async_get_clientsession(hass)
        self._auth_type = auth_type
        self._login = login
        self._password = password
        self._auth_payload = auth_payload
        self._session_cookie = session_cookie

    @property
    def auth_payload(self) -> dict[str, Any] | None:
        """Return the last successful auth payload."""
        return self._auth_payload

    @property
    def auth_token(self) -> str | None:
        """Return the auth token used for authenticated requests."""
        if not self._auth_payload:
            return None
        return self._auth_payload.get("auth")

    @property
    def session_cookie(self) -> str | None:
        """Return the stored session cookie value."""
        return self._session_cookie

    async def async_authenticate(self) -> dict[str, Any]:
        """Authenticate against the EIRC SPB API."""
        url = f"{API_BASE_URL}{API_AUTH_PATH}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Captcha": "none",
            "Content-Type": "application/json",
            "customer": API_CUSTOMER,
        }
        self._apply_session_cookie(headers)
        payload = {
            "type": self._auth_type,
            "login": self._login,
            "password": self._password,
        }

        try:
            async with self._session.post(url, headers=headers, json=payload) as response:
                self._update_session_cookie(response)
                if response.status == 424:
                    data = await response.json(content_type=None)
                    raise EircSpbConfirmationRequired(
                        transaction_id=data["transactionId"],
                        types=data["types"],
                    )

                if response.status in (400, 401, 403):
                    raise EircSpbAuthError("Invalid credentials")

                if response.status >= 400:
                    text = await response.text()
                    raise EircSpbError(f"Unexpected API response: {response.status} {text}")

                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircSpbConnectionError("Unable to connect to EIRC SPB") from err

        self._auth_payload = data
        return data

    async def async_get_current_user(self) -> dict[str, Any]:
        """Fetch the current user profile."""
        return await self._async_get_with_auth(API_CURRENT_USER_PATH)

    async def async_get_account_groups(self) -> list[dict[str, Any]]:
        """Fetch account groups for the current user."""
        data = await self._async_get_with_auth(API_ACCOUNT_GROUPS_PATH)
        if not isinstance(data, list):
            raise EircSpbError("Unexpected account groups payload")
        return data

    async def async_get_account_details(self, account_id: str) -> list[dict[str, Any]]:
        """Fetch details for a personal account."""
        data = await self._async_get_with_auth(
            API_ACCOUNT_DETAILS_PATH.format(account_id=account_id)
        )
        if not isinstance(data, list):
            raise EircSpbError("Unexpected account details payload")
        return data

    async def async_send_confirmation(self, transaction_id: str, challenge_type: str) -> None:
        """Request a confirmation code for a selected challenge type."""
        challenge = challenge_type.lower()
        url = (
            f"{API_BASE_URL}/api/v7/users/{transaction_id}/"
            f"{challenge}/check/confirmation/send"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }
        self._apply_session_cookie(headers)

        try:
            async with self._session.post(url, headers=headers) as response:
                self._update_session_cookie(response)
                if response.status >= 400:
                    text = await response.text()
                    raise EircSpbConfirmationError(
                        f"Unable to send confirmation: {response.status} {text}"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircSpbConnectionError("Unable to connect to EIRC SPB") from err

    async def async_confirm_challenge(
            self,
            transaction_id: str,
            challenge_type: str,
            code: str,
    ) -> dict[str, Any]:
        """Validate a confirmation challenge."""
        challenge = challenge_type.lower()
        url = (
            f"{API_BASE_URL}/api/v7/users/{transaction_id}/"
            f"{challenge}/check/verification"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }
        self._apply_session_cookie(headers)
        payload = {"code": code}

        try:
            async with self._session.post(url, headers=headers, json=payload) as response:
                self._update_session_cookie(response)
                if response.status in (400, 401, 403):
                    raise EircSpbConfirmationError("Invalid confirmation code")

                if response.status >= 400:
                    text = await response.text()
                    raise EircSpbConfirmationError(
                        f"Unable to verify confirmation: {response.status} {text}"
                    )

                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircSpbConnectionError("Unable to connect to EIRC SPB") from err

        self._auth_payload = data
        return data

    async def _async_get_with_auth(self, path: str, retry_auth: bool = True) -> Any:
        """Perform an authenticated GET request."""
        if not self.auth_token:
            await self._async_reauthenticate()

        url = f"{API_BASE_URL}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self.auth_token}",
        }
        self._apply_session_cookie(headers)

        try:
            async with self._session.get(url, headers=headers) as response:
                self._update_session_cookie(response)
                if response.status in (401, 403):
                    if not retry_auth:
                        raise EircSpbAuthError("Invalid auth token")
                    await self._async_reauthenticate()
                    return await self._async_get_with_auth(path, retry_auth=False)

                if response.status >= 400:
                    text = await response.text()
                    raise EircSpbError(f"Unexpected API response: {response.status} {text}")

                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircSpbConnectionError("Unable to connect to EIRC SPB") from err

    def _apply_session_cookie(self, headers: dict[str, str]) -> None:
        """Attach the saved session cookie to request headers."""
        if self._session_cookie:
            headers["Cookie"] = f"session-cookie={self._session_cookie}"

    def _update_session_cookie(self, response: aiohttp.ClientResponse) -> None:
        """Persist session cookie from a response if the server provided one."""
        cookie = response.cookies.get("session-cookie")
        if cookie and cookie.value:
            self._session_cookie = cookie.value

    async def _async_reauthenticate(self) -> None:
        """Refresh authentication tokens using stored credentials."""
        try:
            await self.async_authenticate()
        except EircSpbConfirmationRequired as err:
            raise EircSpbReauthRequired(err.transaction_id, err.types) from err
