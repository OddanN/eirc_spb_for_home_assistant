"""Version: 0.0.1. The EIRC SPB integration."""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import EircSpbApiClient, EircSpbAuthError, EircSpbClientAuthContext
from .const import (
    CONF_ACCESS,
    CONF_ACCOUNT_IDS,
    CONF_ACCOUNT_NAMES,
    CONF_AUTH,
    CONF_AUTH_TYPE,
    CONF_EMAIL,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_SESSION_COOKIE,
    CONF_USER_ID,
    CONF_VERIFIED,
    DOMAIN,
)
from .coordinator import EircSpbDataUpdateCoordinator

type EircSpbConfigEntry = ConfigEntry[EircSpbApiClient]

PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up the integration from YAML."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EircSpbConfigEntry) -> bool:
    """Set up EIRC SPB from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    client = EircSpbApiClient(
        hass,
        EircSpbClientAuthContext(
            auth_type=entry.data[CONF_AUTH_TYPE],
            login=entry.data[CONF_LOGIN],
            password=entry.data[CONF_PASSWORD],
            auth_payload={
                             key: entry.data[key]
                             for key in (CONF_ACCESS, CONF_AUTH, CONF_VERIFIED)
                             if key in entry.data
                         }
                         or None,
            session_cookie=entry.data.get(CONF_SESSION_COOKIE),
        ),
    )

    data_coordinator = EircSpbDataUpdateCoordinator(hass, client, entry)
    try:
        await data_coordinator.async_config_entry_first_refresh()
    except EircSpbAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": data_coordinator,
    }
    entry.runtime_data = client
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await _async_register_account_devices(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EircSpbConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_account_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register account devices for the selected personal accounts."""
    account_ids = entry.options.get(CONF_ACCOUNT_IDS, [])
    account_names: Mapping[str, str] = entry.options.get(CONF_ACCOUNT_NAMES, {})

    device_registry = dr.async_get(hass)
    user_id = entry.data.get(CONF_USER_ID)
    desired_account_identifiers = {
        f"account_{user_id}_{account_id}" if user_id is not None else f"account_{account_id}"
        for account_id in account_ids
    }
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        device_identifiers = {
            identifier
            for domain, identifier in device_entry.identifiers
            if domain == DOMAIN
        }
        if not device_identifiers:
            continue

        if any(identifier.startswith("user_") for identifier in device_identifiers):
            device_registry.async_remove_device(device_entry.id)
            continue

        if device_identifiers.isdisjoint(desired_account_identifiers):
            device_registry.async_remove_device(device_entry.id)

    for account_id in account_ids:
        account_identifier = (
            f"account_{user_id}_{account_id}"
            if user_id is not None
            else f"account_{account_id}"
        )
        device_entry = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, account_identifier)},
            manufacturer="EIRC SPB",
            name=account_names.get(str(account_id), f"Account {account_id}"),
            model="Personal Account",
        )
        device_registry.async_update_device(device_entry.id, sw_version=None)

    await _async_cleanup_stale_entities(
        hass,
        entry,
        {str(account_id) for account_id in account_ids},
    )


async def _async_cleanup_stale_entities(
        hass: HomeAssistant,
        entry: ConfigEntry,
        active_account_ids: set[str],
) -> None:
    """Remove entities that belong to accounts no longer selected."""
    entity_registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        unique_id = entity_entry.unique_id
        if not unique_id or not unique_id.startswith(prefix):
            continue

        remainder = unique_id[len(prefix):]
        account_id, _, _tail = remainder.partition("_")
        if not account_id:
            continue

        if account_id not in active_account_ids:
            entity_registry.async_remove(entity_entry.entity_id)
