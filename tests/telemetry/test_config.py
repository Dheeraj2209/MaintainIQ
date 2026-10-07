"""Tests for MQTT ingest settings (src/telemetry/config.py, contract §7)."""
import pytest

from src.telemetry.config import TelemetrySettings


def test_defaults_disable_ingest():
    s = TelemetrySettings.from_env({})
    assert s.enabled is False
    assert s.broker_label is None
    assert s.broker_port == 1883
    assert s.tls is False and s.ca_cert is None
    assert s.client_id == "maintainiq-ingest"
    assert s.topic_prefix == "maintainiq/v1"
    assert s.auto_register is True
    assert s.ingest_queue_max == 256
    assert s.use_auth is False


def test_full_env():
    s = TelemetrySettings.from_env({
        "MQTT_BROKER_HOST": " mosquitto ",
        "MQTT_BROKER_PORT": "1884",
        "MQTT_USERNAME": "ingest",
        "MQTT_PASSWORD": "s3cret",
        "MQTT_TLS": "yes",
        "MQTT_CA_CERT": "/certs/ca.pem",
        "MQTT_CLIENT_ID": "custom-id",
        "MQTT_TOPIC_PREFIX": "plant/a/",
        "MQTT_AUTO_REGISTER": "0",
        "MQTT_INGEST_QUEUE_MAX": "10",
    })
    assert s.enabled and s.broker_host == "mosquitto"
    assert s.broker_label == "mosquitto:1884"
    assert s.use_auth and s.username == "ingest" and s.password == "s3cret"
    assert s.tls is True and s.ca_cert == "/certs/ca.pem"
    assert s.client_id == "custom-id"
    assert s.topic_prefix == "plant/a"
    assert s.auto_register is False
    assert s.ingest_queue_max == 10


def test_password_not_in_repr():
    s = TelemetrySettings.from_env({"MQTT_USERNAME": "u", "MQTT_PASSWORD": "hunter2"})
    assert "hunter2" not in repr(s)


@pytest.mark.parametrize("value, expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("banana", False),
])
def test_truthy_parsing_matches_email_module(value, expected):
    assert TelemetrySettings.from_env({"MQTT_TLS": value}).tls is expected
    assert TelemetrySettings.from_env({"MQTT_AUTO_REGISTER": value}).auto_register is expected


def test_blank_auto_register_keeps_default_true():
    assert TelemetrySettings.from_env({"MQTT_AUTO_REGISTER": "  "}).auto_register is True


def test_tls_changes_default_port_only_when_unset():
    assert TelemetrySettings.from_env({"MQTT_TLS": "1"}).broker_port == 8883
    assert TelemetrySettings.from_env({"MQTT_TLS": "1", "MQTT_BROKER_PORT": "9000"}).broker_port == 9000


def test_auth_requires_both_username_and_password():
    assert not TelemetrySettings.from_env({"MQTT_USERNAME": "u"}).use_auth
    assert not TelemetrySettings.from_env({"MQTT_PASSWORD": "p"}).use_auth


@pytest.mark.parametrize("env, name", [
    ({"MQTT_BROKER_PORT": "abc"}, "MQTT_BROKER_PORT"),
    ({"MQTT_BROKER_PORT": "0"}, "MQTT_BROKER_PORT"),
    ({"MQTT_BROKER_PORT": "70000"}, "MQTT_BROKER_PORT"),
    ({"MQTT_INGEST_QUEUE_MAX": "0"}, "MQTT_INGEST_QUEUE_MAX"),
    ({"MQTT_INGEST_QUEUE_MAX": "lots"}, "MQTT_INGEST_QUEUE_MAX"),
    ({"MQTT_TOPIC_PREFIX": "a/+/b"}, "wildcards"),
])
def test_invalid_values_raise_naming_the_variable(env, name):
    with pytest.raises(ValueError, match=name):
        TelemetrySettings.from_env(env)


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("MQTT_BROKER_HOST", "broker.local")
    monkeypatch.delenv("MQTT_BROKER_PORT", raising=False)
    monkeypatch.delenv("MQTT_TLS", raising=False)
    s = TelemetrySettings.from_env()
    assert s.broker_label == "broker.local:1883"
