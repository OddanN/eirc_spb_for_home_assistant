"""Version: 0.0.1. Options flow for the EIRC SPB integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import HomeAssistant
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
    EircSpbReauthRequired,
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
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_COOKIE,
    CONF_VERIFIED,
    DEFAULT_SCAN_INTERVAL_HOURS,
)
from .flow_helpers import (
    ChallengeState,
    async_send_confirmation_with_errors,
    confirmation_description_placeholders,
    menu_options_for_challenges,
    async_validate_confirmation_input,
)

_LOGGER = logging.getLogger(__name__)


def build_entry_title(user: Mapping[str, Any]) -> str:
    """Build config entry title from user payload."""
    name = user.get("name") or {}
    last = (name.get("last") or "").strip()
    first = (name.get("first") or "").strip()
    patronymic = (name.get("patronymic") or "").strip()
    email = (user.get("email") or "").strip()
    phone = (user.get("phone") or "").strip()

    initials = ""
    if first:
        initials += f"{first[0]}."
    if patronymic:
        initials += f"{patronymic[0]}."

    display_name = " ".join(part for part in [last, initials] if part).strip()
    contacts = ", ".join(part for part in [email, phone] if part).strip()

    if display_name and contacts:
        return f"{display_name} [{contacts}]"
    if display_name:
        return display_name
    if contacts:
        return contacts
    return "EIRC SPB"


def build_account_name_maps(groups: list[dict[str, Any]]) -> dict[str, str]:
    """Build account labels for selectors."""
    account_map: dict[str, str] = {}
    for group in groups:
        group_name = group["name"]
        for account_id in group.get("accounts", []):
            account_key = str(account_id)
            account_map.setdefault(account_key, f"{group_name}: {account_id}")
    return account_map


class EircSpbOptionsFlow(OptionsFlow):
    """Handle options for EIRC SPB."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry
        self._groups: list[dict[str, Any]] = []
        self._account_map: dict[str, str] = {}
        self._selected_accounts: list[str] = list(config_entry.options.get(CONF_ACCOUNT_IDS, []))
        self._scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS
        )
        self._challenge_state = ChallengeState()

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage integration options."""
        if not self._groups:
            load_result = await self._async_prepare_groups()
            if load_result is not None:
                return load_result

        if user_input is not None:
            self._scan_interval = user_input[CONF_SCAN_INTERVAL]
            new_accounts = user_input[CONF_ACCOUNT_IDS]
            self._selected_accounts = [
                account_id for account_id in new_accounts if account_id in self._account_map
            ]
            selected_account_names = {
                account_id: self._account_map[account_id]
                for account_id in self._selected_accounts
            }
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: self._scan_interval,
                    CONF_ACCOUNT_IDS: self._selected_accounts,
                    CONF_ACCOUNT_NAMES: selected_account_names,
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self._build_schema(),
        )

    async def async_step_reauth_confirmation_method(
            self, _user_input: dict[str, Any] | None = None
    ):
        """Show available confirmation methods for options flow reauth."""
        if not self._challenge_state.challenge_types:
            return self.async_abort(reason="invalid_auth")

        return self.async_show_menu(
            step_id="reauth_confirmation_method",
            menu_options=self._menu_options_for_challenges(),
        )

    async def async_step_email_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ):
        """Send email confirmation for options reauth."""
        return await self._async_step_send_confirmation(AUTH_TYPE_EMAIL)

    async def async_step_phone_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ):
        """Send phone confirmation for options reauth."""
        return await self._async_step_send_confirmation(AUTH_TYPE_PHONE)

    async def async_step_flashcall_confirmation(
            self, _user_input: dict[str, Any] | None = None
    ):
        """Send flashcall confirmation for options reauth."""
        return await self._async_step_send_confirmation(AUTH_TYPE_FLASHCALL)

    async def _async_step_send_confirmation(self, challenge_type: str):
        """Send confirmation code for options reauth."""
        if not self._challenge_state.transaction_id:
            return self.async_abort(reason="invalid_auth")

        send_error = await async_send_confirmation_with_errors(
            self._build_client,
            self._challenge_state.transaction_id,
            challenge_type,
        )
        abort_reason = {
            "cannot_connect": "cannot_connect",
            "confirmation_failed": "unknown",
        }.get(send_error)
        if abort_reason is not None:
            return self.async_abort(reason=abort_reason)

        self._challenge_state.selected_challenge_type = challenge_type
        return await self.async_step_confirmation_code()

    async def async_step_confirmation_code(
            self, user_input: dict[str, Any] | None = None
    ):
        """Handle confirmation code entry for options reauth."""
        if (
                not self._challenge_state.selected_challenge_type
                or not self._challenge_state.transaction_id
        ):
            return self.async_abort(reason="invalid_auth")

        errors, confirmation_result = await async_validate_confirmation_input(
            self._build_client,
            self._challenge_state.transaction_id,
            self._challenge_state.selected_challenge_type,
            user_input,
        )
        if confirmation_result is not None:
            client, payload = confirmation_result
            self._update_config_entry_auth(client, payload)
            self._groups = []
            return await self.async_step_init()

        return self.async_show_form(
            step_id="confirmation_code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            description_placeholders=confirmation_description_placeholders(
                self._challenge_state.selected_challenge_type
            ),
            errors=errors,
        )

    def _build_schema(self) -> vol.Schema:
        """Build the options form schema."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self._scan_interval,
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
                    default=[
                        account_id
                        for account_id in self._selected_accounts
                        if account_id in self._account_map
                    ],
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
        )

    async def _async_load_groups(self, _hass: HomeAssistant) -> None:
        """Load groups from API."""
        client = self._build_client()
        self._groups = await client.async_get_account_groups()
        _LOGGER.debug("Loaded %s account groups", len(self._groups))
        self._account_map = build_account_name_maps(self._groups)
        _LOGGER.debug(
            "Prepared %s accounts for options flow",
            len(self._account_map),
        )

    async def _async_prepare_groups(self):
        """Load groups and map flow exceptions to HA flow results."""
        try:
            await self._async_load_groups(self.hass)
        except EircSpbReauthRequired as err:
            self._challenge_state.transaction_id = err.transaction_id
            self._challenge_state.challenge_types = err.types
            return self.async_show_menu(
                step_id="reauth_confirmation_method",
                menu_options=self._menu_options_for_challenges(),
            )
        except EircSpbAuthError:
            return self.async_abort(reason="invalid_auth")
        except EircSpbConfirmationRequired as err:
            self._challenge_state.transaction_id = err.transaction_id
            self._challenge_state.challenge_types = err.types
            return self.async_show_menu(
                step_id="reauth_confirmation_method",
                menu_options=self._menu_options_for_challenges(),
            )
        except EircSpbConnectionError:
            return self.async_abort(reason="cannot_connect")
        except EircSpbError:
            _LOGGER.exception("Failed to load account groups for options flow")
            return self.async_abort(reason="unknown")
        return None

    def _build_client(self) -> EircSpbApiClient:
        """Build an API client from config entry data."""
        return EircSpbApiClient(
            self.hass,
            EircSpbClientAuthContext(
                auth_type=self._config_entry.data[CONF_AUTH_TYPE],
                login=self._config_entry.data[CONF_LOGIN],
                password=self._config_entry.data[CONF_PASSWORD],
                auth_payload={
                                 key: self._config_entry.data[key]
                                 for key in (CONF_ACCESS, CONF_AUTH, CONF_VERIFIED)
                                 if key in self._config_entry.data
                             }
                             or None,
                session_cookie=self._config_entry.data.get(CONF_SESSION_COOKIE),
            ),
        )

    def _update_config_entry_auth(
            self,
            client: EircSpbApiClient,
            payload: dict[str, Any],
    ) -> None:
        """Update stored auth data after successful reauthentication."""
        self.hass.config_entries.async_update_entry(
            self._config_entry,
            data={
                **self._config_entry.data,
                CONF_CHALLENGE_TYPE: self._challenge_state.selected_challenge_type,
                CONF_ACCESS: payload.get(CONF_ACCESS),
                CONF_AUTH: payload.get(CONF_AUTH),
                CONF_SESSION_COOKIE: client.session_cookie,
                CONF_VERIFIED: payload.get(CONF_VERIFIED),
            },
        )

    def _menu_options_for_challenges(self) -> list[str]:
        """Return options-flow step ids for available challenge types."""
        return menu_options_for_challenges(self._challenge_state.challenge_types)
