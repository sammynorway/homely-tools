import json
from collections import defaultdict

with open("homely-devices.json", encoding="utf-8") as f:
    data = json.load(f)

models = defaultdict(lambda: {
    "count": 0,
    "features": defaultdict(lambda: defaultdict(set))
})

for device in data.get("devices", []):
    model = device.get("modelName", "Unknown")
    models[model]["count"] += 1

    for feature_name, feature in device.get("features", {}).items():
        for state_name, state in feature.get("states", {}).items():
            value = state.get("value")
            value_type = type(value).__name__

            models[model]["features"][feature_name][state_name].add(
                value_type
            )

report = {}

for model, info in sorted(models.items()):
    report[model] = {
        "count": info["count"],
        "features": {
            feature: {
                state: sorted(types)
                for state, types in sorted(states.items())
            }
            for feature, states in sorted(info["features"].items())
        }
    }

with open("homely-schema.json", "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print(f"Fant {len(data.get('devices', []))} enheter")
print(f"Fant {len(report)} forskjellige modeller")
print("Rapport skrevet til homely-schema.json")