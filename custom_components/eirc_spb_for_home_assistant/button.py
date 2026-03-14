"""Button platform for the EIRC SPB integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_IDS, CONF_ACCOUNT_NAMES, CONF_USER_ID, DOMAIN
from .coordinator import EircSpbDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EircSpbDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]['coordinator']
    account_ids = entry.options.get(CONF_ACCOUNT_IDS, [])
    account_names = entry.options.get(CONF_ACCOUNT_NAMES, {})
    async_add_entities(
        EircSpbRefreshButton(entry, coordinator, str(account_id), account_names.get(str(account_id)))
        for account_id in account_ids
    )


class EircSpbRefreshButton(CoordinatorEntity[EircSpbDataUpdateCoordinator], ButtonEntity):
    _attr_name = 'Refresh'
    _attr_icon = 'mdi:refresh'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, coordinator: EircSpbDataUpdateCoordinator, account_id: str,
                 account_name: str | None) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ButtonEntity.__init__(self)
        user_id = entry.data.get(CONF_USER_ID)
        account_identifier = f"account_{user_id}_{account_id}" if user_id is not None else f"account_{account_id}"
        self._attr_unique_id = f"{entry.entry_id}_{account_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_identifier)},
            manufacturer='EIRC SPB',
            model='Personal Account',
            name=account_name or f'Account {account_id}',
        )

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
