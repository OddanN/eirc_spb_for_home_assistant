"""Version: 0.0.1. Coordinator for the EIRC SPB integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EircSpbApiClient, EircSpbAuthError, EircSpbConnectionError, EircSpbError
from .const import (
    CONF_ACCOUNT_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    ISSUE_ID_API_UNAVAILABLE,
    RETRY_504_INTERVAL_MINUTES,
    RETRY_504_MAX_ATTEMPTS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class EircSpbDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinate EIRC SPB account detail updates."""

    def __init__(
            self,
            hass: HomeAssistant,
            client: EircSpbApiClient,
            entry: ConfigEntry,
    ) -> None:
        """Initialize coordinator."""
        self.client = client
        self.entry = entry
        self._default_update_interval = timedelta(
            hours=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS)
        )
        self._gateway_timeout_count = 0
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=self._default_update_interval,
        )

    async def async_restore_last_data(self) -> None:
        """Restore the last successful payload from storage."""
        stored = await self._store.async_load()
        if not stored:
            return

        restored = self._deserialize_data(stored)
        if not restored:
            return

        selected_account_ids = {
            str(account_id) for account_id in self.entry.options.get(CONF_ACCOUNT_IDS, [])
        }
        if selected_account_ids:
            restored = {
                account_id: payload
                for account_id, payload in restored.items()
                if account_id in selected_account_ids
            }
        if not restored:
            return

        self.data = restored
        _LOGGER.debug(
            "Restored cached EIRC SPB data for entry %s from storage",
            self.entry.entry_id,
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch details for all selected accounts."""
        data: dict[str, dict[str, Any]] = {}
        updated_at = dt_util.now()
        try:
            for account_id in self.entry.options.get(CONF_ACCOUNT_IDS, []):
                details = await self.client.async_get_account_details(account_id)
                data[str(account_id)] = {
                    "details": details,
                    "updated_at": updated_at,
                }
        except EircSpbAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EircSpbConnectionError as err:
            if err.status_code == 504 and self.data:
                await self._async_handle_gateway_timeout(err)
            await self._async_delete_api_issue()
            raise UpdateFailed(str(err)) from err
        except EircSpbError as err:
            await self._async_delete_api_issue()
            raise UpdateFailed(str(err)) from err

        self._gateway_timeout_count = 0
        self.update_interval = self._default_update_interval
        await self._async_delete_api_issue()
        await self._store.async_save(self._serialize_data(data))
        return data

    async def _async_handle_gateway_timeout(self, err: EircSpbConnectionError) -> None:
        """Handle repeated 504 responses while keeping stale data."""
        self._gateway_timeout_count += 1
        self.update_interval = timedelta(minutes=RETRY_504_INTERVAL_MINUTES)

        if self._gateway_timeout_count >= RETRY_504_MAX_ATTEMPTS:
            await self._async_create_api_issue()
            raise UpdateFailed(
                "EIRC SPB API returned HTTP 504 repeatedly. Cached data is stale."
            ) from err

        await self._async_delete_api_issue()
        raise UpdateFailed(
            "EIRC SPB API returned HTTP 504. Using cached data and retrying in "
            f"{RETRY_504_INTERVAL_MINUTES} minutes "
            f"({self._gateway_timeout_count}/{RETRY_504_MAX_ATTEMPTS})."
        ) from err

    async def _async_create_api_issue(self) -> None:
        """Create a repair issue after repeated 504 responses."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_ID_API_UNAVAILABLE}_{self.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ID_API_UNAVAILABLE,
            translation_placeholders={
                "entry_title": self.entry.title,
                "attempts": str(RETRY_504_MAX_ATTEMPTS),
                "minutes": str(RETRY_504_INTERVAL_MINUTES),
            },
        )

    async def _async_delete_api_issue(self) -> None:
        """Delete the repair issue if the API recovered."""
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_ID_API_UNAVAILABLE}_{self.entry.entry_id}",
        )

    def _serialize_data(self, data: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Convert runtime coordinator data to storage format."""
        return {
            "accounts": {
                account_id: {
                    "details": payload.get("details", []),
                    "updated_at": payload["updated_at"].isoformat()
                    if payload.get("updated_at")
                    else None,
                }
                for account_id, payload in data.items()
            }
        }

    def _deserialize_data(
            self,
            stored: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Convert stored payload to runtime coordinator data."""
        accounts = stored.get("accounts")
        if not isinstance(accounts, dict):
            return {}

        restored: dict[str, dict[str, Any]] = {}
        for account_id, payload in accounts.items():
            if not isinstance(payload, dict):
                continue

            details = payload.get("details")
            if not isinstance(details, list):
                continue

            updated_at_raw = payload.get("updated_at")
            updated_at = (
                dt_util.parse_datetime(updated_at_raw)
                if isinstance(updated_at_raw, str)
                else None
            )
            restored[str(account_id)] = {
                "details": details,
                "updated_at": updated_at,
            }
        return restored
