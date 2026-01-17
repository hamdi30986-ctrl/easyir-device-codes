"""Utility functions for EasyIR."""
import os
import json
from .const import DOMAIN

def get_device_codes(hass, device_type):
    """Return a list of available codes for a specific device type."""
    # Build the path: config/custom_components/easy_ir/codes/climate
    base_path = hass.config.path("custom_components", DOMAIN, "codes", device_type)
    
    options = []
    
    # Check if directory exists
    if not os.path.exists(base_path):
        return options

    # Scan for .json files
    for filename in os.listdir(base_path):
        if filename.endswith(".json"):
            file_path = os.path.join(base_path, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Extract useful labels for the dropdown
                    code = filename.replace(".json", "")
                    manufacturer = data.get("manufacturer", "Unknown")
                    models = ", ".join(data.get("supportedModels", []))
                    
                    # Format: "1000 - Test Brand (Model X, Model Y)"
                    label = f"{code} - {manufacturer} ({models})"
                    
                    # Add to options list
                    options.append({"value": code, "label": label})
            except Exception:
                continue
                
    # Sort by code
    options.sort(key=lambda x: x["value"])
    return options