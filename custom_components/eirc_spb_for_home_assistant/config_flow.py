"""Version: 0.0.1. Config flow for the EIRC SPB integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    EircSpbApiClient,
    EircSpbAuthError,
    EircSpbClientAuthContext,
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
from .flow_helpers import (
    ChallengeState,
    async_send_confirmation_with_errors,
    confirmation_description_placeholders,
    menu_options_for_challenges,
    async_validate_confirmation_input,
)
from .options_flow import EircSpbOptionsFlow, build_account_name_maps, build_entry_title


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
        self._challenge_state = ChallengeState()
        self._reauth_entry: ConfigEntry | None = None
        self._account_map: dict[str, str] = {}
        self._entry_title: str | None = None
        self._pending_entry_data: dict[str, Any] | None = None

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
        self._challenge_state = ChallengeState()
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
                self.hass,
                EircSpbClientAuthContext(
                    auth_type=auth_type,
                    login=self._login,
                    password=self._password,
                ),
            )

            try:
                payload = await client.async_authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            except EircSpbConfirmationRequired as err:
                self._challenge_state.transaction_id = err.transaction_id
                self._challenge_state.challenge_types = err.types
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
        if not self._challenge_state.challenge_types:
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
        if (
                not self._challenge_state.transaction_id
                or not self._auth_type
                or not self._login
                or not self._password
        ):
            return self._abort(reason="unknown")

        send_error = await async_send_confirmation_with_errors(
            self._build_client,
            self._challenge_state.transaction_id,
            challenge_type,
        )
        if send_error == "cannot_connect":
            return self._abort(reason="cannot_connect")
        if send_error == "confirmation_failed":
            return self._abort(reason="confirmation_send_failed")

        self._challenge_state.selected_challenge_type = challenge_type
        return self._result(await self.async_step_confirmation_code())

    async def async_step_confirmation_code(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle confirmation code entry."""
        if not self._challenge_state.selected_challenge_type:
            return self._abort(reason="unknown")

        errors, confirmation_result = await async_validate_confirmation_input(
            self._build_client,
            self._challenge_state.transaction_id or "",
            self._challenge_state.selected_challenge_type,
            user_input,
        )
        if confirmation_result is not None:
            client, payload = confirmation_result
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
            self._challenge_state.transaction_id = err.transaction_id
            self._challenge_state.challenge_types = err.types
            return self._show_menu(
                step_id="confirmation_method",
                menu_options=self._menu_options_for_challenges(),
            )
        except EircSpbConnectionError:
            return self._abort(reason="cannot_connect")
        except EircSpbError:
            return self._abort(reason="unknown")

        return self._result(await self._async_finish_auth(client, payload))

    async def async_step_settings(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure tracked accounts and scan interval after auth."""
        if self._pending_entry_data is None:
            return self._abort(reason="unknown")

        if user_input is not None:
            selected_accounts = [
                account_id
                for account_id in user_input[CONF_ACCOUNT_IDS]
                if account_id in self._account_map
            ]
            return self._create_entry(
                title=self._entry_title or self._login or "EIRC SPB",
                data=self._pending_entry_data,
                options={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ACCOUNT_IDS: selected_accounts,
                    CONF_ACCOUNT_NAMES: {
                        account_id: self._account_map[account_id]
                        for account_id in selected_accounts
                    },
                },
            )

        return self._show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=DEFAULT_SCAN_INTERVAL_HOURS,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=12,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="h",
                        )
                    ),
                    vol.Required(
                        CONF_ACCOUNT_IDS,
                        default=list(self._account_map.keys()),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=account_id, label=label)
                                for account_id, label in self._account_map.items()
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )


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
            CONF_CHALLENGE_TYPE: self._challenge_state.selected_challenge_type,
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

        groups = await client.async_get_account_groups()
        self._account_map = build_account_name_maps(groups)
        self._entry_title = title
        self._pending_entry_data = data
        return self._result(await self.async_step_settings())

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
            self.hass,
            EircSpbClientAuthContext(
                auth_type=self._auth_type or AUTH_TYPE_EMAIL,
                login=self._login or "",
                password=self._password or "",
                auth_payload=auth_payload or None,
                session_cookie=session_cookie,
            ),
        )

    def _menu_options_for_challenges(self) -> list[str]:
        """Return config flow step ids for available challenge types."""
        return menu_options_for_challenges(self._challenge_state.challenge_types)

    def _confirmation_description_placeholders(self) -> dict[str, str]:
        """Build placeholders for the confirmation code step."""
        return confirmation_description_placeholders(
            self._challenge_state.selected_challenge_type
        )

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
