import os
import sys
import types
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "API"))

from HomelyEntities import HomelyEntityRegistry, entity_definitions

# MQTT_AD_Devices only needs these optional packages at runtime. Lightweight
# module stubs keep this unit test independent of the local Docker environment.
sys.modules.setdefault("yaml", types.ModuleType("yaml"))
sys.modules.setdefault("paho", types.ModuleType("paho"))
sys.modules.setdefault("paho.mqtt", types.ModuleType("paho.mqtt"))
sys.modules.setdefault("paho.mqtt.client", types.ModuleType("paho.mqtt.client"))

from MQTT_AD_Devices import MQTT_AD_Config, MQTT_AD_Device


def device(model, states, device_id="device-1"):
    features = {}
    for (feature, state), value in states.items():
        features.setdefault(feature, {"states": {}})["states"][state] = {
            "value": value,
            "lastUpdated": f"time-{feature}-{state}",
        }
    return {
        "id": device_id,
        "serialNumber": f"serial-{device_id}",
        "name": "Hall",
        "modelName": model,
        "online": True,
        "features": features,
    }


class FakeEntity:
    def __init__(self, definition):
        self.definition = definition
        self.config = {}
        self.config_published = False
        self.messages = []
        self.availability = []

    def device_config_append(self, attributes):
        self.config.update(attributes)

    def device_config_publish(self):
        self.config_published = True

    def device_message(self, message, timestamp=None):
        self.messages.append((message, timestamp))
        return True

    def device_availability(self, online):
        self.availability.append(online)
        return True


class FakeMQTT:
    def __init__(self):
        self.published = []
        self.wait_count = 0

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )
        mqtt = self

        class PublishResult:
            def wait_for_publish(self):
                mqtt.wait_count += 1

        return PublishResult()


class HomelyEntityTests(unittest.TestCase):
    def make_registry(self):
        created = {}

        def factory(source_device, definition):
            entity = FakeEntity(definition)
            created[(source_device["id"], definition.feature, definition.state)] = entity
            return entity

        return HomelyEntityRegistry(factory), created

    def test_known_alarm_models_use_their_actual_state_names(self):
        cases = (
            ("Alarm Entry Sensor 2", "alarm", "door"),
            ("Alarm Motion Sensor 2", "alarm", "motion"),
            ("Intelligent Smoke Alarm", "fire", "smoke"),
            ("Water Leak Detector", "flood", "moisture"),
        )
        for model, state, component in cases:
            definitions = entity_definitions(device(model, {("alarm", state): False}))
            alarm = next(item for item in definitions if item.feature == "alarm")
            self.assertEqual(alarm.state, state)
            self.assertEqual(alarm.component, component)
            self.assertEqual(alarm.object_name, "alarm")
            if model == "Alarm Entry Sensor 2":
                self.assertEqual(alarm.display_name, "Kontakt")

    def test_rest_and_websocket_share_the_same_registered_entity(self):
        source = device(
            "Intelligent Smoke Alarm",
            {
                ("alarm", "fire"): False,
                ("temperature", "temperature"): 21.5,
                ("battery", "voltage"): 2.9,
                ("diagnostic", "networklinkstrength"): 81,
            },
        )
        registry, created = self.make_registry()
        registry.publish_home({"devices": [source]})

        fire = created[("device-1", "alarm", "fire")]
        self.assertEqual(fire.messages[-1][0], "OFF")
        registry.publish_change(
            "device-1",
            {"feature": "alarm", "stateName": "fire", "value": True, "lastUpdated": "later"},
        )
        self.assertEqual(fire.messages[-1], ("ON", "later"))

        temperature = created[("device-1", "temperature", "temperature")]
        self.assertEqual(temperature.messages[-1][0], '{"temperature": 21.5}')

    def test_yale_report_values_are_mapped(self):
        source = device(
            "Yale Doorman V2N",
            {
                ("lock", "state"): "LOCKED",
                ("report", "locked"): True,
                ("report", "doorclosed"): False,
                ("report", "lowbat"): True,
                ("report", "event"): "manual-lock",
                ("report", "errorcode"): 0,
            },
        )
        registry, created = self.make_registry()
        registry.publish_home({"devices": [source]})

        self.assertEqual(created[("device-1", "report", "locked")].messages[-1][0], "OFF")
        self.assertEqual(created[("device-1", "report", "doorclosed")].messages[-1][0], "ON")
        self.assertEqual(created[("device-1", "report", "lowbat")].messages[-1][0], "ON")
        self.assertEqual(created[("device-1", "report", "event")].messages[-1][0], "manual-lock")

    def test_legacy_unique_ids_are_preserved(self):
        source = device("Alarm Entry Sensor 2", {("alarm", "alarm"): False})
        source["features"]["temperature"] = {
            "states": {"temperature": {"value": 20.0}}
        }
        definitions = entity_definitions(source)
        object_names = {
            (definition.feature, definition.state): definition.object_name
            for definition in definitions
        }

        # MQTT_AD_Device builds unique_id from object_name. These legacy values
        # therefore keep the IDs created by the pre-v2 implementation.
        self.assertEqual(object_names[("alarm", "alarm")], "alarm")
        self.assertEqual(object_names[("temperature", "temperature")], "temperature")

    def test_mqtt_state_is_retained_for_late_home_assistant_subscription(self):
        mqtt = FakeMQTT()
        MQTT_AD_Config("Hjemme", mqtt, state_topic="homely", discovery_delay=0)
        mqtt_device = MQTT_AD_Device("Vindu bar", "Alarm Entry Sensor 2", "alarm", "door")

        mqtt_device.device_message("ON", timestamp="2026-10-11T15:11:54Z")

        self.assertEqual(mqtt.published[-1]["payload"], "ON")
        self.assertTrue(mqtt.published[-1]["retain"])
        self.assertEqual(mqtt.published[-1]["qos"], 1)

    def test_read_only_entities_do_not_advertise_command_topics(self):
        mqtt = FakeMQTT()
        MQTT_AD_Config("Hjemme", mqtt, state_topic="homely", discovery_delay=0)

        alarm = MQTT_AD_Device(
            "Vindu bar",
            "Alarm Entry Sensor 2",
            "alarm",
            "alarm_event",
            entity_name="Alarm",
        )
        voltage = MQTT_AD_Device(
            "Vindu bar",
            "Alarm Entry Sensor 2",
            "battery_voltage",
            "voltage",
            entity_name="Batterispenning",
        )
        selector = MQTT_AD_Device("Gateway", "Alarm", "alarmstate", "selector")

        self.assertNotIn("command_topic", alarm.config)
        self.assertNotIn("command_topic", voltage.config)
        self.assertEqual(alarm.config["name"], "Alarm")
        self.assertEqual(voltage.config["name"], "Batterispenning")
        self.assertIn("command_topic", selector.config)

    def test_discovery_waits_for_qos_one_and_publishes_availability(self):
        mqtt = FakeMQTT()
        MQTT_AD_Config("Hjemme", mqtt, state_topic="homely", discovery_delay=0)
        voltage = MQTT_AD_Device(
            "Vindu bar",
            "Alarm Entry Sensor 2",
            "battery_voltage",
            "voltage",
            entity_name="Batterispenning",
        )

        voltage.device_config_publish()
        voltage.device_availability(True)

        discovery, availability = mqtt.published
        self.assertEqual(discovery["qos"], 1)
        self.assertTrue(discovery["retain"])
        self.assertEqual(mqtt.wait_count, 2)
        self.assertEqual(
            voltage.config["availability_topic"],
            availability["topic"],
        )
        self.assertEqual(availability["payload"], "online")
        self.assertEqual(availability["qos"], 1)
        self.assertTrue(availability["retain"])

    def test_tamper_is_discovered_when_present(self):
        source = device(
            "Alarm Entry Sensor 2",
            {("alarm", "alarm"): False, ("alarm", "tamper"): True},
        )
        definitions = entity_definitions(source)
        tamper = next(item for item in definitions if item.state == "tamper")

        self.assertEqual(tamper.object_name, "tamper")
        self.assertEqual(tamper.component, "tamper")
        self.assertEqual(tamper.discovery["entity_category"], "diagnostic")


if __name__ == "__main__":
    unittest.main()
