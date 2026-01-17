"""Support for EasyIR Climate devices."""
from __future__ import annotations

import json
import logging
import asyncio

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, CONF_TEMPERATURE_SENSOR, CONF_DEVICE_CODE, CONF_CONTROLLER

_LOGGER = logging.getLogger(__name__)

# Map JSON strings to Home Assistant Constants
HVAC_MODES_MAP = {
    "off": HVACMode.OFF,
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "fan_only": HVACMode.FAN_ONLY,
}

# Inverse map for sending commands (HA Constant -> JSON String)
HVAC_MODES_INV_MAP = {v: k for k, v in HVAC_MODES_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EasyIR Climate platform."""
    device_code = entry.data.get(CONF_DEVICE_CODE)
    
    # Build path to the JSON file
    json_path = hass.config.path(
        "custom_components", DOMAIN, "codes", "climate", f"{device_code}.json"
    )

    try:
        with open(json_path, "r") as f:
            device_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        _LOGGER.error(f"EasyIR: Could not load device file {json_path}: {e}")
        return

    # Initialize the entity
    device = EasyIRClimate(hass, entry, device_data)
    async_add_entities([device])


class EasyIRClimate(ClimateEntity):
    """Representation of an EasyIR Climate device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_data: dict) -> None:
        """Initialize the climate device."""
        self.hass = hass
        self._entry = entry
        self._device_data = device_data
        self._controller = entry.data.get(CONF_CONTROLLER)

        self._attr_name = entry.data["name"]
        self._attr_unique_id = entry.entry_id
        self._sensor_entity = entry.data.get(CONF_TEMPERATURE_SENSOR)

        # 1. Set Limits from JSON
        self._attr_min_temp = device_data.get("minTemperature", 16)
        self._attr_max_temp = device_data.get("maxTemperature", 30)
        self._attr_target_temperature_step = device_data.get("precision", 1)
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS

        # 2. Set Modes (Fix: Ensure OFF is always added if command exists)
        self._attr_hvac_modes = [
            HVAC_MODES_MAP[mode]
            for mode in device_data.get("operationModes", [])
            if mode in HVAC_MODES_MAP
        ]

        if "off" in device_data.get("commands", {}) and HVACMode.OFF not in self._attr_hvac_modes:
            self._attr_hvac_modes.append(HVACMode.OFF)

        # 3. Set Fan Modes
        raw_fan_modes = device_data.get("fanModes", [])
        if raw_fan_modes:
            self._attr_fan_modes = raw_fan_modes
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.FAN_MODE
            )
        else:
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TURN_ON
            )

        # Initial Default State
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 24
        self._attr_fan_mode = raw_fan_modes[0] if raw_fan_modes else "auto"

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register listeners."""
        if self._sensor_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._sensor_entity], self._on_temp_sensor_change
                )
            )

    @callback
    def _on_temp_sensor_change(self, event) -> None:
        """Update current temperature when the source sensor changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        try:
            self._attr_current_temperature = float(new_state.state)
            self.async_write_ha_state()
        except ValueError:
            pass

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        self._attr_hvac_mode = hvac_mode
        await self._async_send_ir()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = temp
            await self._async_send_ir()
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        self._attr_fan_mode = fan_mode
        await self._async_send_ir()
        self.async_write_ha_state()

    async def _async_send_ir(self):
        """Find the code and send it via remote.send_command."""
        
        # 1. Handle OFF Case
        if self._attr_hvac_mode == HVACMode.OFF:
            command = self._device_data["commands"].get("off")
            if command:
                _LOGGER.debug(f"EasyIR: Found OFF command. Length: {len(command) if command else 0}")
                await self._send_command(command)
            else:
                _LOGGER.error("EasyIR: 'off' command not found in JSON!")
            return

        # 2. Prepare keys for lookup
        mode_key = HVAC_MODES_INV_MAP.get(self._attr_hvac_mode)
        fan_key = self._attr_fan_mode
        temp_key = str(int(self._attr_target_temperature))

        # 3. Traverse the JSON structure
        commands = self._device_data.get("commands", {})
        
        # Validation to prevent crashes
        if mode_key not in commands:
            _LOGGER.error(f"EasyIR: Mode '{mode_key}' missing from JSON.")
            return
            
        if fan_key not in commands[mode_key]:
            # Fallback: Try the first available fan mode if the current one is missing
            available_fans = list(commands[mode_key].keys())
            _LOGGER.warning(f"EasyIR: Fan '{fan_key}' missing. Retrying with '{available_fans[0]}'")
            fan_key = available_fans[0]
            
        if temp_key not in commands[mode_key][fan_key]:
            _LOGGER.error(f"EasyIR: Temp '{temp_key}' missing from JSON.")
            return

        # If we get here, we found the code
        code = commands[mode_key][fan_key][temp_key]
        
        if code:
            await self._send_command(code)

    async def _send_command(self, code):
        """Call the remote.send_command service with Broadlink fix."""
        
        # Ensure code is a list
        if isinstance(code, str):
            code = [code]

        # FIX: Broadlink requires 'b64:' prefix for base64 codes
        final_commands = []
        for c in code:
            if isinstance(c, str) and not c.startswith("b64:"):
                final_commands.append(f"b64:{c}")
            else:
                final_commands.append(c)

        _LOGGER.info(f"EasyIR: Sending {len(final_commands)} command(s) to {self._controller}")

        # Call the service
        await self.hass.services.async_call(
            "remote",
            "send_command",
            {
                "entity_id": self._controller,
                "command": final_commands,
            }
        )