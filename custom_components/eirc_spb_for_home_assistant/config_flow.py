"""Version: 0.0.1. Config flow for the EIRC SPB integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD

from .api import (
    EircSpbApiClient,
    EircSpbAuthError,
    EircSpbConfirmationError,
    EircSpbConfirmationRequired,
    EircSpbConnectionError,
    EircSpbError,
)
from .const import (
    AUTH_TYPE_EMAIL,
    AUTH_TYPE_FLASHCALL,
    AUTH_TYPE_PHONE,
    CONF_ACCESS,
    CONF_ACCOUNT_IDS,
    CONF_ACCOUNT_NAMES,
    CONF_AUTH,
    CONF_AUTH_TYPE,
    CONF_CHALLENGE_TYPE,
    CONF_CODE,
    CONF_EMAIL,
    CONF_LOGIN,
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_USER_ID,
    CONF_VERIFIED,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .options_flow import EircSpbOptionsFlow, build_entry_title


def _normalize_login(auth_type: str, login: str) -> str:
    """Normalize login value for storage and unique IDs."""
    login = login.strip()
    if auth_type == AUTH_TYPE_EMAIL:
        return login.lower()
    has_plus = login.startswith("+")
    digits = re.sub(r"\D", "", login)
    return f"+{digits}" if has_plus else digits


def _is_valid_phone(login: str) -> bool:
    """Validate normalized phone format."""
    return bool(re.fullmatch(r"\+\d{11}", login))


# noinspection PyTypeChecker
class EircSpbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EIRC SPB."""

    VERSION = 1

    def is_matching(self, _other_flow: ConfigFlow) -> bool:
        """Return whether another flow matches this one."""
        return False

    def __init__(self) -> None:
        """Initialize the flow."""
        self._auth_type: str | None = None
        self._login: str | None = None
        self._password: str | None = None
        self._transaction_id: str | None = None
        self._challenge_types: list[str] = []
        self._selected_challenge_type: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
            self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        return self._show_menu(step_id="user", menu_options=["email", "phone"])

    async def async_step_email(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle email authentication."""
        return self._result(await self._async_step_auth(AUTH_TYPE_EMAIL, "email", user_input))

    async def async_step_phone(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle phone authentication."""
        return self._result(await self._async_step_auth(AUTH_TYPE_PHONE, "phone", user_input))

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication triggered by Home Assistant."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self._reauth_entry is None:
            return self._abort(reason="unknown")

        self._auth_type = self._reauth_entry.data[CONF_AUTH_TYPE]
        self._login = self._reauth_entry.data[CONF_LOGIN]
        self._password = self._reauth_entry.data[CONF_PASSWORD]
        self._transaction_id = None
        self._challenge_types = []
        self._selected_challenge_type = None
        return self._result(await self._async_try_reauth())

    async def async_step_reauth_confirm(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for password during reauthentication."""
        if self._reauth_entry is None or self._login is None:
            return self._abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            return self._result(await self._async_try_reauth())

        return self._show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"login": self._login},
            errors=errors,
        )

    async def _async_step_auth(
            self,
            auth_type: str,
            step_id: str,
            user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Handle an authentication step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._auth_type = auth_type
            self._login = _normalize_login(auth_type, user_input[CONF_LOGIN])
            self._password = user_input[CONF_PASSWORD]

            if auth_type == AUTH_TYPE_PHONE and not _is_valid_phone(self._login):
                errors[CONF_LOGIN] = "invalid_phone"
                return self._show_form(
                    step_id=step_id,
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_LOGIN, default=user_input[CONF_LOGIN]): str,
                            vol.Required(CONF_PASSWORD, default=self._password): str,
                        }
                    ),
                    errors=errors,
                )

            if self._reauth_entry is None:
                await self.async_set_unique_id(f"{auth_type}:{self._login}")
                self._abort_if_unique_id_configured()

            client = EircSpbApiClient(
                hass=self.hass,
                auth_type=auth_type,
                login=self._login,
                password=self._password,
            )

            try:
                payload = await client.async_authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            except EircSpbConfirmationRequired as err:
                self._transaction_id = err.transaction_id
                self._challenge_types = err.types
                return self._show_menu(
                    step_id="confirmation_method",
                    menu_options=self._menu_options_for_challenges(),
                )
            except EircSpbConnectionError:
                errors["base"] = "cannot_connect"
            except EircSpbError:
                errors["base"] = "unknown"
            else:
                try:
                    return self._result(await self._async_finish_auth(client, payload))
                except EircSpbConnectionError:
                    errors["base"] = "cannot_connect"
                except EircSpbAuthError:
                    errors["base"] = "invalid_auth"
                except EircSpbError:
                    errors["base"] = "unknown"

        return self._show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_confirmation_method(
            self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fallback step if HA enters the method step directly."""
        if not self._challenge_types:
            return self._abort(reason="unknown")

        return self._show_menu(
            step_id="confirmation_method",
            menu_options=self._menu_options_for_challenges(),
        )

    async def async_step_email_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send email confirmation and show code form."""
        return self._result(await self._async_step_send_confirmation(AUTH_TYPE_EMAIL))

    async def async_step_phone_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send phone confirmation and show code form."""
        return self._result(await self._async_step_send_confirmation(AUTH_TYPE_PHONE))

    async def async_step_flashcall_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send flashcall confirmation and show code form."""
        return self._result(await self._async_step_send_confirmation(AUTH_TYPE_FLASHCALL))

    async def _async_step_send_confirmation(self, challenge_type: str) -> ConfigFlowResult:
        """Send confirmation for the selected challenge type."""
        if not self._transaction_id or not self._auth_type or not self._login or not self._password:
            return self._abort(reason="unknown")

        client = self._build_client()
        try:
            await client.async_send_confirmation(self._transaction_id, challenge_type)
        except EircSpbConnectionError:
            return self._abort(reason="cannot_connect")
        except EircSpbConfirmationError:
            return self._abort(reason="confirmation_send_failed")

        self._selected_challenge_type = challenge_type
        return self._result(await self.async_step_confirmation_code())

    async def async_step_confirmation_code(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle confirmation code entry."""
        if not self._selected_challenge_type:
            return self._abort(reason="unknown")

        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input[CONF_CODE].strip()
            expected_length = 4 if self._selected_challenge_type == AUTH_TYPE_FLASHCALL else 5
            if len(code) != expected_length:
                errors[CONF_CODE] = "invalid_code_length"
            else:
                client = self._build_client()
                try:
                    payload = await client.async_confirm_challenge(
                        self._transaction_id,
                        self._selected_challenge_type,
                        code,
                    )
                except EircSpbConfirmationError:
                    errors["base"] = "invalid_confirmation_code"
                except EircSpbConnectionError:
                    errors["base"] = "cannot_connect"
                except EircSpbError:
                    errors["base"] = "unknown"
                else:
                    try:
                        return self._result(await self._async_finish_auth(client, payload))
                    except EircSpbConnectionError:
                        errors["base"] = "cannot_connect"
                    except EircSpbAuthError:
                        errors["base"] = "invalid_auth"
                    except EircSpbError:
                        errors["base"] = "unknown"

        return self._show_form(
            step_id="confirmation_code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders=self._confirmation_description_placeholders(),
        )

    async def _async_try_reauth(self) -> ConfigFlowResult:
        """Try reauthentication with stored or updated credentials."""
        if self._reauth_entry is None:
            return self._abort(reason="unknown")

        client = self._build_client()
        try:
            payload = await client.async_authenticate()
        except EircSpbAuthError:
            return self._show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                description_placeholders={"login": self._login or ""},
                errors={"base": "invalid_auth"},
            )
        except EircSpbConfirmationRequired as err:
            self._transaction_id = err.transaction_id
            self._challenge_types = err.types
            return self._show_menu(
                step_id="confirmation_method",
                menu_options=self._menu_options_for_challenges(),
            )
        except EircSpbConnectionError:
            return self._abort(reason="cannot_connect")
        except EircSpbError:
            return self._abort(reason="unknown")

        return self._result(await self._async_finish_auth(client, payload))

    async def _async_finish_auth(
            self,
            client: EircSpbApiClient,
            payload: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Create or update an entry after successful authentication."""
        if payload is None:
            payload = {}

        user = await client.async_get_current_user()
        title = build_entry_title(user)
        data = {
            CONF_AUTH_TYPE: self._auth_type,
            CONF_LOGIN: self._login,
            CONF_PASSWORD: self._password,
            CONF_CHALLENGE_TYPE: self._selected_challenge_type,
            CONF_ACCESS: payload.get(CONF_ACCESS),
            CONF_AUTH: payload.get(CONF_AUTH),
            CONF_EMAIL: user.get(CONF_EMAIL),
            CONF_PHONE: user.get(CONF_PHONE),
            CONF_SESSION_COOKIE: client.session_cookie,
            CONF_USER_ID: user.get("userId"),
            CONF_VERIFIED: payload.get(CONF_VERIFIED),
        }

        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                title=title,
                data=data,
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self._abort(reason="reauth_successful")

        return self._create_entry(
            title=title,
            data=data,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_HOURS,
                CONF_ACCOUNT_IDS: [],
                CONF_ACCOUNT_NAMES: {},
            },
        )

    def _build_client(self) -> EircSpbApiClient:
        """Build an API client from current flow state."""
        auth_payload = None
        session_cookie = None
        if self._reauth_entry is not None:
            auth_payload = {
                key: self._reauth_entry.data.get(key)
                for key in (CONF_ACCESS, CONF_AUTH, CONF_VERIFIED)
                if self._reauth_entry.data.get(key) is not None
            }
            session_cookie = self._reauth_entry.data.get(CONF_SESSION_COOKIE)

        return EircSpbApiClient(
            hass=self.hass,
            auth_type=self._auth_type or AUTH_TYPE_EMAIL,
            login=self._login or "",
            password=self._password or "",
            auth_payload=auth_payload or None,
            session_cookie=session_cookie,
        )

    def _menu_options_for_challenges(self) -> list[str]:
        """Return config flow step ids for available challenge types."""
        mapping = {
            AUTH_TYPE_EMAIL: "email_confirmation",
            AUTH_TYPE_PHONE: "phone_confirmation",
            AUTH_TYPE_FLASHCALL: "flashcall_confirmation",
        }
        return [
            mapping[challenge_type]
            for challenge_type in self._challenge_types
            if challenge_type in mapping
        ]

    def _confirmation_description_placeholders(self) -> dict[str, str]:
        """Build placeholders for the confirmation code step."""
        placeholders = {
            "code_length": "4" if self._selected_challenge_type == AUTH_TYPE_FLASHCALL else "5",
            "flashcall_hint": "",
        }
        if self._selected_challenge_type == AUTH_TYPE_FLASHCALL:
            placeholders["flashcall_hint"] = (
                "Last 4 digits of the phone number that called you."
            )
        return placeholders

    @staticmethod
    def _result(result: Any) -> ConfigFlowResult:
        """Return a flow result with an explicit Home Assistant type."""
        return result

    def _show_form(
            self,
            *,
            step_id: str,
            data_schema: vol.Schema,
            errors: dict[str, str] | None = None,
            description_placeholders: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Typed wrapper around async_show_form."""
        return self._result(
            self.async_show_form(
                step_id=step_id,
                data_schema=data_schema,
                errors=errors,
                description_placeholders=description_placeholders,
            )
        )

    def _show_menu(self, *, step_id: str, menu_options: list[str]) -> ConfigFlowResult:
        """Typed wrapper around async_show_menu."""
        return self._result(
            self.async_show_menu(step_id=step_id, menu_options=menu_options)
        )

    def _abort(self, *, reason: str) -> ConfigFlowResult:
        """Typed wrapper around async_abort."""
        return self._result(self.async_abort(reason=reason))

    def _create_entry(
            self,
            *,
            title: str,
            data: dict[str, Any],
            options: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Typed wrapper around async_create_entry."""
        return self._result(
            self.async_create_entry(title=title, data=data, options=options)
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EircSpbOptionsFlow:
        """Get the options flow for this handler."""
        return EircSpbOptionsFlow(config_entry)
