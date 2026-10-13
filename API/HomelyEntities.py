"""Declarative Homely device mappings and a shared MQTT update pipeline."""

from dataclasses import dataclass, field
import json
import logging


@dataclass(frozen=True)
class EntityDefinition:
    feature: str
    state: str
    object_name: str
    component: str
    value_kind: str = "raw"
    discovery: dict = field(default_factory=dict)
    display_name: str = None


# These entities are exposed whenever the corresponding feature/state exists.
# The object names for alarm and temperature intentionally match the legacy
# implementation so Home Assistant keeps the existing unique IDs.
COMMON_ENTITIES = (
    EntityDefinition("temperature", "temperature", "temperature", "temp", "temperature", display_name="Temperatur"),
    EntityDefinition("alarm", "tamper", "tamper", "tamper", "binary", {"entity_category": "diagnostic"}, "Sabotasje"),
    EntityDefinition("battery", "low", "battery_low", "battery", "binary", {"entity_category": "diagnostic"}, "Lavt batteri"),
    EntityDefinition("battery", "defect", "battery_defect", "problem", "binary", {"entity_category": "diagnostic"}, "Batterifeil"),
    EntityDefinition("battery", "voltage", "battery_voltage", "voltage", "raw", {"entity_category": "diagnostic"}, "Batterispenning"),
    EntityDefinition("diagnostic", "networklinkstrength", "network_link_strength", "link_quality", "raw", {"entity_category": "diagnostic"}, "Signalstyrke"),
    EntityDefinition("diagnostic", "networklinkaddress", "network_link_address", "network_address", "raw", {"entity_category": "diagnostic"}, "Nettverksadresse"),
)


MODEL_ENTITIES = {
    "Alarm Entry Sensor 2": (
        # Homely emits a short alarm pulse; it is not a persistent open/closed state.
        # Present it as a contact in Home Assistant for the familiar open/closed UI.
        EntityDefinition("alarm", "alarm", "alarm", "door", "binary", display_name="Kontakt"),
    ),
    "Alarm Motion Sensor 2": (
        EntityDefinition("alarm", "alarm", "alarm", "motion", "binary", display_name="Bevegelse"),
    ),
    "Intelligent Smoke Alarm": (
        EntityDefinition("alarm", "fire", "alarm", "smoke", "binary", display_name="Røykalarm"),
    ),
    "Water Leak Detector": (
        EntityDefinition("alarm", "flood", "alarm", "moisture", "binary", display_name="Vannlekkasje"),
    ),
    "Yale Doorman V2N": (
        EntityDefinition("lock", "state", "lock_state", "lock_status", display_name="Låsestatus"),
        # Home Assistant's lock binary-sensor is ON when unlocked.
        EntityDefinition("report", "locked", "locked", "lock", "inverted_binary", display_name="Lås"),
        # A door binary-sensor is ON when open.
        EntityDefinition("report", "doorclosed", "door_closed", "door", "inverted_binary", display_name="Dør"),
        EntityDefinition("report", "lowbat", "low_battery", "battery", "binary", {"entity_category": "diagnostic"}, "Lavt batteri"),
        EntityDefinition("report", "event", "last_event", "event", display_name="Siste hendelse"),
        EntityDefinition("report", "errorcode", "error_code", "error_code", "raw", {"entity_category": "diagnostic"}, "Feilkode"),
    ),
    # Kept for compatibility with models supported before v2.
    "Motion Sensor Mini": (
        EntityDefinition("alarm", "alarm", "alarm", "motion", "binary", display_name="Bevegelse"),
    ),
    "Window Sensor": (
        EntityDefinition("alarm", "alarm", "alarm", "door", "binary", display_name="Kontakt"),
    ),
}


def _state_data(device, feature, state):
    return (
        device.get("features", {})
        .get(feature, {})
        .get("states", {})
        .get(state)
    )


def entity_definitions(device):
    """Return definitions that are both mapped and present on the device."""
    definitions = COMMON_ENTITIES + MODEL_ENTITIES.get(device.get("modelName"), ())
    return tuple(
        definition
        for definition in definitions
        if _state_data(device, definition.feature, definition.state) is not None
    )


def encode_value(definition, value):
    if value is None:
        return None
    if definition.value_kind == "temperature":
        return json.dumps({"temperature": value})
    if definition.value_kind in ("binary", "inverted_binary"):
        if isinstance(value, str):
            enabled = value.strip().lower() in ("1", "true", "on", "yes", "locked", "closed")
        else:
            enabled = bool(value)
        if definition.value_kind == "inverted_binary":
            enabled = not enabled
        return "ON" if enabled else "OFF"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


class HomelyEntityRegistry:
    """Creates discovery entities and routes REST and WebSocket values to them."""

    def __init__(self, device_factory, logger=None):
        self.device_factory = device_factory
        self.logger = logger or logging.getLogger(__name__)
        self.entities = {}

    def register_home(self, home_state):
        for device in home_state.get("devices", []):
            self.register_device(device)

    def register_device(self, device):
        device_id = device["id"]
        for definition in entity_definitions(device):
            route = (device_id, definition.feature, definition.state)
            if route in self.entities:
                continue
            mqtt_device = self.device_factory(device, definition)
            if definition.discovery:
                mqtt_device.device_config_append(definition.discovery)
            mqtt_device.device_config_publish()
            self.entities[route] = (definition, mqtt_device)

    def publish_home(self, home_state):
        # Complete discovery first, then publish availability and state. This
        # avoids racing Home Assistant while it creates its MQTT subscriptions.
        self.register_home(home_state)
        for device in home_state.get("devices", []):
            self.publish_availability(device)
            self.publish_device(device)

    def publish_availability(self, device):
        device_id = device["id"]
        for route, (_, mqtt_device) in self.entities.items():
            if route[0] == device_id:
                mqtt_device.device_availability(device.get("online", False))
                return True
        return False

    def publish_device(self, device):
        device_id = device["id"]
        for definition in entity_definitions(device):
            state_data = _state_data(device, definition.feature, definition.state)
            if not isinstance(state_data, dict) or "value" not in state_data:
                continue
            self.publish_value(
                device_id,
                definition.feature,
                definition.state,
                state_data["value"],
                state_data.get("lastUpdated"),
            )

    def publish_change(self, device_id, change):
        feature = change.get("feature")
        state = change.get("stateName")
        route = (device_id, feature, state)

        # Some API versions omit feature in WebSocket events. State names are
        # unique for the mapped entities on the known models, so fall back safely.
        if route not in self.entities and feature is None:
            matches = [key for key in self.entities if key[0] == device_id and key[2] == state]
            if len(matches) == 1:
                route = matches[0]

        entity = self.entities.get(route)
        if entity is None:
            self.logger.debug("Ignoring unmapped Homely change %s", change)
            return False

        definition, mqtt_device = entity
        message = encode_value(definition, change.get("value"))
        if message is None:
            return False
        return mqtt_device.device_message(message, timestamp=change.get("lastUpdated"))

    def publish_value(self, device_id, feature, state, value, timestamp=None):
        entity = self.entities.get((device_id, feature, state))
        if entity is None:
            return False
        definition, mqtt_device = entity
        message = encode_value(definition, value)
        if message is None:
            return False
        return mqtt_device.device_message(message, timestamp=timestamp)
