"""Version: 0.0.1. Coordinator for the EIRC SPB integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import EircSpbApiClient, EircSpbAuthError
from .const import CONF_ACCOUNT_IDS, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN

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
        update_interval = timedelta(
            hours=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS)
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=update_interval,
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
        return data
