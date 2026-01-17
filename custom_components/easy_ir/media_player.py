"""Support for EasyIR Media Player devices."""
from __future__ import annotations

import json
import logging
import asyncio

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaPlayerDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_CODE, CONF_CONTROLLER

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EasyIR Media Player platform."""
    device_code = entry.data.get(CONF_DEVICE_CODE)
    
    # Path to the JSON file
    json_path = hass.config.path(
        "custom_components", DOMAIN, "codes", "media_player", f"{device_code}.json"
    )

    try:
        with open(json_path, "r") as f:
            device_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        _LOGGER.error(f"EasyIR: Could not load device file {json_path}: {e}")
        return

    device = EasyIRMediaPlayer(hass, entry, device_data)
    async_add_entities([device])


class EasyIRMediaPlayer(MediaPlayerEntity):
    """Representation of an EasyIR Media Player (TV or Sound System)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_data: dict) -> None:
        """Initialize the media player."""
        self.hass = hass
        self._entry = entry
        self._device_data = device_data
        self._controller = entry.data.get(CONF_CONTROLLER)
        self._attr_name = entry.data["name"]
        self._attr_unique_id = entry.entry_id
        
        # Default to TV, but this works for Speakers too
        self._attr_device_class = MediaPlayerDeviceClass.TV

        # 1. Determine Features dynamically based on JSON keys
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_OFF | MediaPlayerEntityFeature.TURN_ON
        )
        
        commands = device_data.get("commands", {})

        # If it has Volume codes -> Enable Volume Slider/Buttons
        if "volumeDown" in commands and "volumeUp" in commands:
            self._attr_supported_features |= (
                MediaPlayerEntityFeature.VOLUME_STEP | MediaPlayerEntityFeature.VOLUME_MUTE
            )

        # If it has Source codes -> Enable Source Dropdown
        if "sources" in commands:
            self._attr_supported_features |= MediaPlayerEntityFeature.SELECT_SOURCE
            self._attr_source_list = sorted(list(commands["sources"].keys()))

        # If it has Play/Pause -> Enable Media Controls
        if "play" in commands and "pause" in commands:
             self._attr_supported_features |= (
                 MediaPlayerEntityFeature.PLAY | 
                 MediaPlayerEntityFeature.PAUSE | 
                 MediaPlayerEntityFeature.STOP
             )
        
        # Initial State (Assume OFF safely)
        self._attr_state = STATE_OFF
        self._attr_source = None

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self._send_command("on")
        self._attr_state = STATE_ON
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self._send_command("off")
        self._attr_state = STATE_OFF
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        """Turn volume up."""
        await self._send_command("volumeUp")

    async def async_volume_down(self) -> None:
        """Turn volume down."""
        await self._send_command("volumeDown")

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        await self._send_command("mute")

    async def async_media_play(self) -> None:
        """Send play command."""
        await self._send_command("play")

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self._send_command("pause")
        
    async def async_media_stop(self) -> None:
        """Send stop command."""
        await self._send_command("stop")

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        if source in self._device_data.get("commands", {}).get("sources", {}):
            code = self._device_data["commands"]["sources"][source]
            await self._send_raw_code(code)
            self._attr_source = source
            self.async_write_ha_state()

    async def _send_command(self, command_key: str):
        """Helper to find the code in the main dict and send it."""
        code = self._device_data.get("commands", {}).get(command_key)
        
        if not code and command_key == "on":
             code = self._device_data.get("commands", {}).get("power")
             
        if code:
            await self._send_raw_code(code)
        else:
            _LOGGER.warning(f"EasyIR: Command '{command_key}' not found in device JSON.")

    async def _send_raw_code(self, code):
        """Send the actual base64 code to the Broadlink entity."""
        if isinstance(code, str):
            code = [code]

        final_commands = []
        for c in code:
            if isinstance(c, str) and not c.startswith("b64:"):
                final_commands.append(f"b64:{c}")
            else:
                final_commands.append(c)

        await self.hass.services.async_call(
            "remote",
            "send_command",
            {
                "entity_id": self._controller,
                "command": final_commands,
            }
        )