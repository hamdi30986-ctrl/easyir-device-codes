import os
import json

# Define the directories to scan
subfolders = ["climate", "media_player", "fan"]

for folder in subfolders:
    path = os.path.join("codes", folder)
    if not os.path.exists(path):
        continue
    
    index_data = []
    
    print(f"Scanning {folder}...")
    
    for filename in os.listdir(path):
        if filename.endswith(".json") and filename != "index.json":
            try:
                with open(os.path.join(path, filename), "r") as f:
                    data = json.load(f)
                    
                    # Extract info
                    entry = {
                        "code": filename.replace(".json", ""),
                        "manufacturer": data.get("manufacturer", "Unknown"),
                        "supported_models": data.get("supportedModels", []),
                    }
                    index_data.append(entry)
            except Exception as e:
                print(f"Skipping {filename}: {e}")

    # Sort by manufacturer then code
    index_data.sort(key=lambda x: (x["manufacturer"], x["code"]))

    # Save index.json in the same folder
    output_path = os.path.join(path, "index.json")
    with open(output_path, "w") as f:
        json.dump(index_data, f, indent=2)
    
    print(f"Created {output_path} with {len(index_data)} entries.")