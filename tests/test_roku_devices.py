from __future__ import annotations

from database import connect as connect_database
from playback.roku import _parse_device_info
import roku_devices


def _device(*, device_id: str, serial: str, name: str, host: str) -> tuple[str, dict]:
    return host, {
        "device_id": device_id,
        "serial_number": serial,
        "name": name,
        "model": "Roku TV",
        "model_number": "T1234",
        "software_version": "15.0.0",
    }


def test_device_info_exposes_stable_identity():
    payload = b"""<?xml version="1.0" encoding="UTF-8" ?>
    <device-info>
      <user-device-name>Living Room</user-device-name>
      <model-name>65-inch Roku TV</model-name>
      <model-number>65R6A5R</model-number>
      <serial-number>YN00ABC12345</serial-number>
      <device-id>S0ABC12345</device-id>
      <software-version>15.0.0</software-version>
    </device-info>
    """
    info = _parse_device_info(payload)
    assert info["name"] == "Living Room"
    assert info["device_id"] == "S0ABC12345"
    assert info["serial_number"] == "YN00ABC12345"


def test_multiple_roku_devices_are_saved_separately(tmp_path):
    db_path = tmp_path / "picker.db"
    connect_database(db_path).close()

    host1, info1 = _device(device_id="DEV-A", serial="SER-A", name="Living Room", host="10.0.0.41")
    host2, info2 = _device(device_id="DEV-B", serial="SER-B", name="Bedroom", host="10.0.0.52")

    first = roku_devices.save_device(db_path, host1, info1)
    second = roku_devices.save_device(db_path, host2, info2)
    saved = roku_devices.list_saved(db_path)

    assert first["device_key"] == "device:DEV-A"
    assert second["device_key"] == "device:DEV-B"
    assert {item["device_key"] for item in saved} == {"device:DEV-A", "device:DEV-B"}
    assert {item["host"] for item in saved} == {"10.0.0.41", "10.0.0.52"}


def test_reconcile_updates_saved_roku_ip_without_creating_unsaved_devices(tmp_path):
    db_path = tmp_path / "picker.db"
    connect_database(db_path).close()

    old_host, saved_info = _device(device_id="DEV-A", serial="SER-A", name="Living Room", host="10.0.0.41")
    roku_devices.save_device(db_path, old_host, saved_info)

    discovered = [
        {"host": "10.0.0.77", **saved_info},
        {
            "host": "10.0.0.88",
            "device_id": "DEV-B",
            "serial_number": "SER-B",
            "name": "Bedroom",
            "model": "Roku Ultra",
            "model_number": "4802X",
            "software_version": "15.0.0",
        },
    ]

    annotated = roku_devices.reconcile_discovered(db_path, discovered)
    saved = roku_devices.list_saved(db_path)

    living_room = next(item for item in annotated if item["device_id"] == "DEV-A")
    bedroom = next(item for item in annotated if item["device_id"] == "DEV-B")

    assert living_room["saved"] is True
    assert living_room["host"] == "10.0.0.77"
    assert bedroom["saved"] is False
    assert len(saved) == 1
    assert saved[0]["device_key"] == "device:DEV-A"
    assert saved[0]["host"] == "10.0.0.77"


def test_serial_number_is_fallback_identity(tmp_path):
    db_path = tmp_path / "picker.db"
    connect_database(db_path).close()

    info = {
        "device_id": "",
        "serial_number": "SER-ONLY",
        "name": "Old Roku",
        "model": "Roku",
        "model_number": "",
        "software_version": "",
    }
    saved = roku_devices.save_device(db_path, "10.0.0.90", info)
    assert saved["device_key"] == "serial:SER-ONLY"
