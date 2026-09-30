from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path
import sys
import types
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CUSTOM = ROOT / "custom_components"
ARTEMIS = CUSTOM / "artemis"

# Load the pure helper modules without importing custom_components.artemis.__init__,
# which depends on Home Assistant and is validated separately by Hassfest.
custom_pkg = types.ModuleType("custom_components")
custom_pkg.__path__ = [str(CUSTOM)]
sys.modules.setdefault("custom_components", custom_pkg)
artemis_pkg = types.ModuleType("custom_components.artemis")
artemis_pkg.__path__ = [str(ARTEMIS)]
sys.modules.setdefault("custom_components.artemis", artemis_pkg)

models = importlib.import_module("custom_components.artemis.models")
helpers = importlib.import_module("custom_components.artemis.helpers")


class PlanningTests(unittest.TestCase):
    def test_operational_day_and_next_change(self) -> None:
        payload = {
            "planning": {
                "fireUnit": {"planningStartTime": "07:00:00"},
                "staff": [
                    {
                        "planningStartDate": "2026-09-30",
                        "planningPeriods": [
                            {
                                "startTime": "00:00:00",
                                "endTime": "12:00:00",
                                "state": {"code": "IND", "name": "INDISPONIBLE"},
                            },
                            {
                                "startTime": "12:00:00",
                                "endTime": "14:00:00",
                                "state": {"code": "DI1", "name": "DISPONIBLE NIV 1"},
                            },
                        ],
                    }
                ],
            }
        }
        tz = ZoneInfo("Europe/Paris")
        intervals = helpers.normalize_planning([payload], tz)
        snap = helpers.build_planning_snapshot(
            intervals,
            datetime(2026, 9, 30, 11, 0, tzinfo=tz),
            staff_name="TEST USER",
            staff_id="1",
            unit_id="TEST",
            lookahead_weeks=1,
        )
        self.assertEqual(snap.current.code, "IND")
        self.assertEqual(snap.next_status.code, "DI1")
        self.assertEqual(snap.next_change, datetime(2026, 9, 30, 19, 0, tzinfo=tz))

    def test_same_status_boundary_is_not_a_change(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        status_ind = models.StatusValue("IND", "INDISPONIBLE")
        status_di = models.StatusValue("DI1", "DISPONIBLE NIV 1")
        intervals = [
            models.PlanningInterval(
                datetime(2026, 9, 30, 7, 0, tzinfo=tz),
                datetime(2026, 9, 30, 19, 0, tzinfo=tz),
                status_ind,
            ),
            models.PlanningInterval(
                datetime(2026, 9, 30, 19, 0, tzinfo=tz),
                datetime(2026, 10, 1, 7, 0, tzinfo=tz),
                status_ind,
            ),
            models.PlanningInterval(
                datetime(2026, 10, 1, 7, 0, tzinfo=tz),
                datetime(2026, 10, 1, 9, 0, tzinfo=tz),
                status_di,
            ),
        ]
        snap = helpers.build_planning_snapshot(
            intervals,
            datetime(2026, 9, 30, 18, 0, tzinfo=tz),
            staff_name="TEST USER",
            staff_id="1",
            unit_id="TEST",
            lookahead_weeks=1,
        )
        self.assertEqual(snap.next_change, datetime(2026, 10, 1, 7, 0, tzinfo=tz))


class OperationTests(unittest.TestCase):
    def test_notification_payload(self) -> None:
        operation = {
            "id": 42,
            "number": "26000042",
            "dateCreation": "2026-09-30T11:43:00",
            "disasterLabel": "SECOURS A PERSONNE",
            "state": {"code": "EC", "name": "EN COURS"},
            "address": {
                "city": "BEAUFORT",
                "streetNumber": "12",
                "streetCategory": "RUE",
                "streetName": "EXEMPLE",
                "cp": "39190",
            },
            "fireUnits": [
                {
                    "shortname": "BEAUF",
                    "state": {"code": "PA", "name": "PARTI"},
                }
            ],
            "vehicles": [
                {
                    "name": "VSAV BEAUFORT",
                    "type": {"name": "VSAV"},
                    "state": {"code": "PA", "name": "PARTI"},
                    "estimatedTime": "11:48",
                }
            ],
        }
        data = helpers.operation_event_data(operation)
        self.assertEqual(data["id"], "42")
        self.assertIn("🚒", data["title"])
        self.assertIn("📍", data["message"])
        self.assertIn("VSAV BEAUFORT", data["message"])


if __name__ == "__main__":
    unittest.main()
