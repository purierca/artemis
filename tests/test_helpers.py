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

    def test_cycle_status_code(self) -> None:
        self.assertEqual(helpers.cycle_status_code("IND"), "DI1")
        self.assertEqual(helpers.cycle_status_code("DI1"), "AS1")
        self.assertEqual(helpers.cycle_status_code("AS1"), "IND")
        self.assertIsNone(helpers.cycle_status_code("GP"))

    def test_status_override_spans_days_and_preserves_next_change(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        payload = {
            "planning": {
                "fireUnit": {"planningStartTime": "07:00:00"},
                "staff": [
                    {
                        "id": "ME",
                        "planningId": 1,
                        "planningStartDate": "2026-10-03",
                        "priority": 1,
                        "name": "USER",
                        "firstName": "TEST",
                        "planningPeriods": [
                            {
                                "startTime": "00:00:00",
                                "endTime": "23:59:59",
                                "state": {"code": "AS1"},
                            }
                        ],
                    },
                    {
                        "id": "ME",
                        "planningId": 2,
                        "planningStartDate": "2026-10-04",
                        "priority": 1,
                        "name": "USER",
                        "firstName": "TEST",
                        "planningPeriods": [
                            {
                                "startTime": "00:00:00",
                                "endTime": "23:59:59",
                                "state": {"code": "AS1"},
                            }
                        ],
                    },
                    {
                        "id": "ME",
                        "planningId": 3,
                        "planningStartDate": "2026-10-05",
                        "priority": 1,
                        "name": "USER",
                        "firstName": "TEST",
                        "planningPeriods": [
                            {
                                "startTime": "00:00:00",
                                "endTime": "02:00:00",
                                "state": {"code": "AS1"},
                            },
                            {
                                "startTime": "02:00:00",
                                "endTime": "23:59:59",
                                "state": {"code": "IND"},
                            },
                        ],
                    },
                ],
            }
        }
        updates = helpers.build_status_override_requests(
            [payload],
            staff_id="ME",
            start=datetime(2026, 10, 3, 14, 38, tzinfo=tz),
            end=datetime(2026, 10, 5, 9, 0, tzinfo=tz),
            new_status_code="DI1",
            tz=tz,
        )
        self.assertEqual(len(updates), 3)

        first = updates[0]["staffMember"]["planningPeriods"]
        self.assertEqual(first[0]["state"]["code"], "AS1")
        self.assertEqual(
            (first[0]["realEndTimeHour"], first[0]["realEndTimeMinute"]),
            ("14", "38"),
        )
        self.assertEqual(first[1]["state"]["code"], "DI1")

        middle = updates[1]["staffMember"]["planningPeriods"]
        self.assertEqual([item["state"]["code"] for item in middle], ["DI1"])

        last = updates[2]["staffMember"]["planningPeriods"]
        self.assertEqual(last[0]["state"]["code"], "DI1")
        self.assertEqual(
            (last[0]["realEndTimeHour"], last[0]["realEndTimeMinute"]),
            ("9", "0"),
        )
        self.assertEqual(last[1]["state"]["code"], "IND")



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
                    "id": "BEA",
                    "shortname": "BEAUF",
                    "state": {"code": "PA", "name": "PARTI"},
                }
            ],
            "vehicles": [
                {
                    "name": "VLTU 01",
                    "ownerFireUnit": {"id": "BEA"},
                    "type": {"name": "VLTU"},
                    "state": {"code": "PA", "name": "PARTI"},
                    "estimatedTime": "11:48",
                }
            ],
        }
        data = helpers.operation_event_data(operation)
        self.assertEqual(data["id"], "42")
        self.assertIn("🚒", data["title"])
        self.assertTrue(data["message"].startswith("\U0001f4cd "))
        self.assertIn("VLTU 01", data["message"])
        self.assertTrue(data["active"])
        self.assertEqual(data["lifecycle"], "updated")
        self.assertEqual(data["state_code"], "EC")
        self.assertEqual(data["state_name"], "EN COURS")
        self.assertEqual(data["vehicles_data"][0]["center"], "BEAUF")
        self.assertEqual(data["vehicles_data"][0]["name"], "VLTU 01")
        self.assertEqual(data["vehicles_data"][0]["state_code"], "PA")

    def test_ended_operation_keeps_last_payload_but_marks_inactive(self) -> None:
        operation = {
            "id": 43,
            "disasterLabel": "FEU",
            "state": {"code": "EC", "name": "EN COURS"},
            "vehicles": [{"name": "FPT 01", "state": {"code": "RE", "name": "RETOUR"}}],
        }
        data = helpers.operation_event_data(
            operation,
            lifecycle="ended",
            active=False,
        )
        self.assertFalse(data["active"])
        self.assertEqual(data["lifecycle"], "ended")
        self.assertEqual(data["state"], "EC \u2013 EN COURS")
        self.assertEqual(data["vehicles_data"][0]["state"], "RE \u2013 RETOUR")


if __name__ == "__main__":
    unittest.main()
