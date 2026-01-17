"""Helper to download device codes from GitHub."""
import os
import logging
import json
import aiohttp

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, CODE_REPO_URL

_LOGGER = logging.getLogger(__name__)

async def get_available_codes(hass, device_type):
    """
    Download the index.json for the specific device type.
    """
    url = f"{CODE_REPO_URL}codes/{device_type}/index.json"
    
    # Verify the URL in logs
    _LOGGER.debug(f"EasyIR: Fetching index from {url}")

    session = async_get_clientsession(hass)

    try:
        async with session.get(url) as response:
            if response.status != 200:
                _LOGGER.error(f"EasyIR: Failed to download index from {url}. Status: {response.status}")
                return []
            
            # Use text() instead of json() to handle content-type mismatches
            text_data = await response.text()
            
            try:
                data = json.loads(text_data)
                return data
            except json.JSONDecodeError as e:
                _LOGGER.error(f"EasyIR: Downloaded index is not valid JSON: {e}")
                return []
            
    except Exception as e:
        _LOGGER.error(f"EasyIR: Error fetching index: {e}")
        return []

async def download_device_code(hass, device_type, code):
    """
    Download the specific JSON file and save it locally.
    """
    url = f"{CODE_REPO_URL}codes/{device_type}/{code}.json"
    local_path = hass.config.path("custom_components", DOMAIN, "codes", device_type, f"{code}.json")
    
    _LOGGER.info(f"EasyIR: Downloading code {code} from {url}")

    session = async_get_clientsession(hass)

    try:
        async with session.get(url) as response:
            if response.status != 200:
                _LOGGER.error(f"EasyIR: Code file {url} not found.")
                return False
            
            content = await response.read()
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Save to file
            with open(local_path, 'wb') as f:
                f.write(content)
                
            return True
            
    except Exception as e:
        _LOGGER.error(f"EasyIR: Failed to download code file: {e}")
        return False