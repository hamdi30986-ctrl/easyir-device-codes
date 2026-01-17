"""Config flow for EasyIR integration."""
from __future__ import annotations

import voluptuous as vol
import os
import logging
import json

from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_DEVICE_TYPE,
    CONF_CONTROLLER,
    CONF_DEVICE_CODE,
    CONF_TEMPERATURE_SENSOR,
)
from .utils import get_device_codes
from .downloader import get_available_codes, download_device_code

DEVICE_TYPES = ["climate", "media_player"]
_LOGGER = logging.getLogger(__name__)

class EasyIRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EasyIR."""

    VERSION = 1

    def __init__(self):
        """Initialize the flow."""
        self.init_info = {}
        self.cached_all_codes = [] 
        self.last_search_query = ""
        
        # Persistence Variables
        self.selected_controller = None
        self.selected_sensor = None
        self.selected_code = None     # <--- The code currently being tested

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Step 1: Name and Device Type."""
        if user_input is not None:
            self.init_info = user_input
            return await self.async_step_controller()

        return self.async_show_form(
            step_id="user", 
            data_schema=vol.Schema({
                vol.Required("name"): str,
                vol.Required(CONF_DEVICE_TYPE, default="climate"): vol.In(DEVICE_TYPES),
            })
        )

    async def async_step_controller(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Step 2: Search & Select."""
        errors = {}
        device_type = self.init_info[CONF_DEVICE_TYPE]
        filtered_options = []
        current_search = self.last_search_query

        if user_input is not None:
            self.selected_controller = user_input.get(CONF_CONTROLLER)
            self.selected_sensor = user_input.get(CONF_TEMPERATURE_SENSOR)
            new_search = user_input.get("search_query", "").strip()
            
            # 1. Search Logic
            if new_search.lower() != self.last_search_query.lower():
                self.last_search_query = new_search
                current_search = new_search
            
            # 2. Selection Logic -> GO TO TEST STEP
            elif user_input.get(CONF_DEVICE_CODE):
                self.selected_code = user_input[CONF_DEVICE_CODE]
                
                # Ensure file exists (Download if needed)
                path = self.hass.config.path("custom_components", DOMAIN, "codes", device_type, f"{self.selected_code}.json")
                if not os.path.exists(path):
                    success = await download_device_code(self.hass, device_type, self.selected_code)
                    if not success:
                        errors["base"] = "download_failed"
                    else:
                        return await self.async_step_test() # <--- Divert to Test
                else:
                    return await self.async_step_test()     # <--- Divert to Test

        # --- Fetch Data & Build Form (Same as before) ---
        if not self.cached_all_codes:
            local_options = await self.hass.async_add_executor_job(
                get_device_codes, self.hass, device_type
            )
            cloud_data = await get_available_codes(self.hass, device_type)
            existing_ids = {opt["value"] for opt in local_options}
            cloud_options = []
            for entry in cloud_data:
                code = str(entry.get("code"))
                if code not in existing_ids:
                    man = entry.get("manufacturer", "Unknown")
                    models = entry.get("supported_models", ["Generic"])
                    model_str = models[0] if isinstance(models, list) and models else str(models)
                    cloud_options.append({"value": code, "label": f"{man} - {model_str} ({code}) ☁️"})
            self.cached_all_codes = local_options + cloud_options

        if current_search:
            search_lower = current_search.lower()
            filtered_options = [opt for opt in self.cached_all_codes if search_lower in opt["label"].lower() or search_lower in opt["value"]]
            if not filtered_options: errors["base"] = "no_codes_found"
        
        # Build Schema
        schema = {}
        default_controller = self.selected_controller or vol.UNDEFINED
        schema[vol.Required(CONF_CONTROLLER, default=default_controller)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="remote")
        )
        schema[vol.Optional("search_query", default=current_search)] = str
        
        if filtered_options:
            filtered_options.sort(key=lambda x: x["label"])
            schema[vol.Required(CONF_DEVICE_CODE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=filtered_options, mode=selector.SelectSelectorMode.DROPDOWN, translation_key="device_code")
            )
        
        default_sensor = self.selected_sensor or vol.UNDEFINED
        schema[vol.Optional(CONF_TEMPERATURE_SENSOR, default=default_sensor)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_number"])
        )

        return self.async_show_form(step_id="controller", data_schema=vol.Schema(schema), errors=errors, description_placeholders={"count": str(len(filtered_options))})

    async def async_step_test(self, user_input: dict[str, any] | None = None) -> FlowResult:
        """Step 3: Test the selected code."""
        errors = {}
        device_type = self.init_info[CONF_DEVICE_TYPE]
        
        # 1. Parse the JSON to get available commands (e.g., "off", "power", "mute")
        json_path = self.hass.config.path("custom_components", DOMAIN, "codes", device_type, f"{self.selected_code}.json")
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                commands = data.get("commands", {})
        except:
            return self.async_abort(reason="file_corruption")

        # 2. Handle Button Clicks
        if user_input is not None:
            action = user_input.get("test_action")
            
            if action == "save":
                # USER IS HAPPY -> SAVE
                final_data = {
                    **self.init_info,
                    CONF_CONTROLLER: self.selected_controller,
                    CONF_DEVICE_CODE: self.selected_code,
                    CONF_TEMPERATURE_SENSOR: self.selected_sensor
                }
                return self.async_create_entry(title=self.init_info["name"], data=final_data)
            
            elif action == "back":
                # USER IS UNHAPPY -> GO BACK
                return await self.async_step_controller()
            
            else:
                # USER WANTS TO TEST A COMMAND (e.g. "test_off")
                # Extract command key (remove "test_" prefix)
                cmd_key = action.replace("test_", "")
                
                # Fetch the raw code
                raw_code = None
                
                # Logic to find code based on type
                if device_type == "media_player":
                    raw_code = commands.get(cmd_key)
                else: 
                    # Climate is complex (nested dicts). We grab a "Safe" test code.
                    # Usually "off" is safe. Or "cool/high/24".
                    if cmd_key == "off":
                        raw_code = commands.get("off")
                    elif cmd_key == "cool":
                        # Try to grab a standard cool command: cool -> auto -> 24
                        try:
                            # We need to find valid keys dynamically
                            # Structure: commands -> mode -> fan -> temp
                            modes = [k for k in commands.keys() if k not in ["off"]]
                            if modes:
                                mode = modes[0] # e.g. "cool"
                                fans = list(commands[mode].keys())
                                if fans:
                                    fan = fans[0] # e.g. "auto"
                                    temps = list(commands[mode][fan].keys())
                                    if temps:
                                        raw_code = commands[mode][fan][temps[0]]
                        except:
                            pass

                # SEND SIGNAL
                if raw_code:
                    if isinstance(raw_code, str): raw_code = [raw_code]
                    final_cmds = [f"b64:{c}" if not c.startswith("b64:") else c for c in raw_code]
                    
                    await self.hass.services.async_call(
                        "remote", "send_command",
                        {"entity_id": self.selected_controller, "command": final_cmds}
                    )
                    # Stay on page, show success message?
                    # Config flows don't have "toast" notifications, but we reload the form.
                else:
                    errors["base"] = "cmd_not_found"

        # 3. Build Test Options (What can they test?)
        test_options = ["save", "back"]
        
        # Dynamically add test buttons based on available commands
        if device_type == "media_player":
            if "power" in commands or "on" in commands: test_options.insert(0, "test_on")
            if "off" in commands: test_options.insert(0, "test_off")
            if "volumeUp" in commands: test_options.insert(0, "test_volumeUp")
            if "mute" in commands: test_options.insert(0, "test_mute")
        else:
            # Climate
            if "off" in commands: test_options.insert(0, "test_off")
            test_options.insert(0, "test_cool") # Represents a generic "On" test

        # 4. Display Form
        return self.async_show_form(
            step_id="test",
            data_schema=vol.Schema({
                vol.Required("test_action"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        # We use a list of translation keys for the dropdown
                        options=[{"value": opt, "label": f"btn_{opt}"} for opt in test_options],
                        mode=selector.SelectSelectorMode.LIST, # Shows as Radio buttons (easier to click)
                        translation_key="test_action"
                    )
                )
            }),
            errors=errors,
            description_placeholders={"code_name": str(self.selected_code)}
        )