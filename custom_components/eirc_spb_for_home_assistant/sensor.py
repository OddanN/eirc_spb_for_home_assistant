"""Version: 0.0.1. Sensor platform for the EIRC SPB integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import CONF_ACCOUNT_NAMES, CONF_USER_ID, DOMAIN
from .coordinator import EircSpbDataUpdateCoordinator

LIVING_PREMISES_HEADER = (
    "\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f "
    "\u043e \u0436\u0438\u043b\u043e\u043c \u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0438"
)


@dataclass(frozen=True)
class EircSpbSensorDescription:
    """Description of a generated EIRC SPB sensor."""

    account_id: str
    block_index: int
    item_index: int
    block_header: str
    name: str


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EIRC SPB sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[EircSpbAccountDetailSensor] = []
    for account_id, payload in coordinator.data.items():
        for description in _build_descriptions(account_id, payload["details"]):
            entities.append(EircSpbAccountDetailSensor(coordinator, entry, description))

    async_add_entities(entities)


def _build_descriptions(
        account_id: str,
        details: list[dict[str, Any]],
) -> list[EircSpbSensorDescription]:
    """Build sensor descriptions from account details blocks."""
    descriptions: list[EircSpbSensorDescription] = []
    for block_index, block in enumerate(details):
        block_header = block.get("header") or "Account Details"
        if block_header != LIVING_PREMISES_HEADER:
            continue
        for item_index, item in enumerate(block.get("content", [])):
            name = item.get("name")
            if not name:
                continue
            descriptions.append(
                EircSpbSensorDescription(
                    account_id=account_id,
                    block_index=block_index,
                    item_index=item_index,
                    block_header=block_header,
                    name=name,
                )
            )
    return descriptions


class EircSpbAccountDetailSensor(
    CoordinatorEntity[EircSpbDataUpdateCoordinator], SensorEntity
):
    """Representation of an EIRC SPB account detail sensor."""

    _attr_has_entity_name = True

    def __init__(
            self,
            coordinator: EircSpbDataUpdateCoordinator,
            entry: ConfigEntry,
            description: EircSpbSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._description = description
        self._attr_name = description.name
        account_label = entry.options.get(CONF_ACCOUNT_NAMES, {}).get(
            description.account_id, description.account_id
        )
        self._attr_unique_id = (
            f"{entry.entry_id}_{description.account_id}_"
            f"{slugify(description.block_header)}_{slugify(description.name)}_"
            f"{description.block_index}_{description.item_index}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    _account_identifier(
                        entry.data.get(CONF_USER_ID),
                        description.account_id,
                    ),
                )
            },
            name=account_label,
        )

    @property
    def native_value(self) -> str | None:
        """Return the current sensor value."""
        item = self._current_item
        if item is None:
            return None
        value = item.get("value")
        if value is None:
            return "0"
        if value == "":
            return "0"
        return str(value)

    @property
    def available(self) -> bool:
        """Keep the sensor available while the last item payload is present."""
        return self._current_item is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        payload = self.coordinator.data.get(self._description.account_id, {})
        updated_at = payload.get("updated_at")
        attrs = {
            "last_update": updated_at.isoformat() if updated_at else None,
            "block_header": self._description.block_header,
        }
        item = self._current_item
        if item is None:
            return attrs

        attrs["real_value"] = item.get("value")
        description = item.get("description")
        if description:
            attrs["description"] = description
        code = item.get("code")
        if code:
            attrs["code"] = code
        return attrs

    @property
    def _current_item(self) -> dict[str, Any] | None:
        """Return the current source item if it still exists in coordinator data."""
        payload = self.coordinator.data.get(self._description.account_id)
        if not payload:
            return None

        details = payload.get("details", [])
        if self._description.block_index >= len(details):
            return None

        content = details[self._description.block_index].get("content", [])
        if self._description.item_index >= len(content):
            return None

        return content[self._description.item_index]


def _account_identifier(user_id: int | None, account_id: str) -> str:
    """Build the device registry identifier for an account."""
    if user_id is not None:
        return f"account_{user_id}_{account_id}"
    return f"account_{account_id}"
