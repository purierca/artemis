from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
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
        )
        self.assertEqual(snap.next_change, datetime(2026, 10, 1, 7, 0, tzinfo=tz))

    def test_no_future_different_status_has_no_next_change(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        status_ind = models.StatusValue("IND", "INDISPONIBLE")
        intervals = [
            models.PlanningInterval(
                datetime(2026, 10, 5, 7, 0, tzinfo=tz),
                datetime(2026, 10, 6, 7, 0, tzinfo=tz),
                status_ind,
            ),
            models.PlanningInterval(
                datetime(2026, 10, 6, 7, 0, tzinfo=tz),
                datetime(2026, 10, 7, 7, 0, tzinfo=tz),
                status_ind,
            ),
        ]
        snap = helpers.build_planning_snapshot(
            intervals,
            datetime(2026, 10, 5, 16, 0, tzinfo=tz),
            staff_name="TEST USER",
            staff_id="1",
            unit_id="TEST",
        )
        self.assertEqual(snap.current.code, "IND")
        self.assertIsNone(snap.next_change)
        self.assertIsNone(snap.next_status)

    def test_status_override_boundary_stops_at_next_handover(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        planning_start = helpers.parse_clock("07:00:00")

        self.assertEqual(
            helpers.status_override_boundary(
                datetime(2026, 10, 19, 9, 30, tzinfo=tz),
                planning_start,
                None,
            ),
            datetime(2026, 10, 19, 19, 0, tzinfo=tz),
        )
        self.assertEqual(
            helpers.status_override_boundary(
                datetime(2026, 10, 19, 21, 0, tzinfo=tz),
                planning_start,
                None,
            ),
            datetime(2026, 10, 20, 7, 0, tzinfo=tz),
        )

    def test_status_override_boundary_prefers_earlier_real_change(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        planning_start = helpers.parse_clock("07:00:00")
        now = datetime(2026, 10, 19, 9, 30, tzinfo=tz)

        self.assertEqual(
            helpers.status_override_boundary(
                now,
                planning_start,
                datetime(2026, 10, 19, 16, 0, tzinfo=tz),
            ),
            datetime(2026, 10, 19, 16, 0, tzinfo=tz),
        )
        self.assertEqual(
            helpers.status_override_boundary(
                now,
                planning_start,
                datetime(2026, 10, 20, 7, 0, tzinfo=tz),
            ),
            datetime(2026, 10, 19, 19, 0, tzinfo=tz),
        )

    def test_status_override_boundary_clamps_legacy_distant_boundary(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        planning_start = helpers.parse_clock("07:00:00")
        now = datetime(2026, 10, 19, 9, 30, tzinfo=tz)

        self.assertEqual(
            helpers.status_override_boundary(
                now,
                planning_start,
                None,
                datetime(2026, 11, 30, 7, 0, tzinfo=tz),
            ),
            datetime(2026, 10, 19, 19, 0, tzinfo=tz),
        )

    def test_fresh_row_detects_programmed_change_before_handover(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        planning_start = helpers.parse_clock("07:00:00")
        row = {
            "planningStartDate": "2026-10-19",
            "planningPeriods": [
                {
                    "startTime": "00:00:00",
                    "endTime": "09:00:00",
                    "state": {"code": "IND", "name": "INDISPONIBLE"},
                },
                {
                    "startTime": "09:00:00",
                    "endTime": "12:00:00",
                    "state": {"code": "AS1", "name": "ASTREINTE NIV 1"},
                },
                {
                    "startTime": "12:00:00",
                    "endTime": "23:59:59",
                    "state": {"code": "IND", "name": "INDISPONIBLE"},
                },
            ],
        }
        change, status = helpers.next_staff_row_status_change(
            row,
            planning_start,
            datetime(2026, 10, 19, 9, 30, tzinfo=tz),
            tz,
        )
        self.assertEqual(change, datetime(2026, 10, 19, 16, 0, tzinfo=tz))
        self.assertEqual(status.code, "AS1")

    def test_status_write_preserves_programmed_periods_after_boundary(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        row = {
            "id": "ME",
            "planningId": 123,
            "planningStartDate": "2026-10-19",
            "priority": 1,
            "planningPeriods": [
                {
                    "startTime": "00:00:00",
                    "endTime": "09:00:00",
                    "state": {"code": "IND"},
                },
                {
                    "startTime": "09:00:00",
                    "endTime": "12:00:00",
                    "state": {"code": "AS1"},
                },
                {
                    "startTime": "12:00:00",
                    "endTime": "23:59:59",
                    "state": {"code": "IND"},
                },
            ],
        }
        request = helpers.build_status_override_request(
            row,
            staff_id="ME",
            planning_start=helpers.parse_clock("07:00:00"),
            start=datetime(2026, 10, 19, 9, 30, tzinfo=tz),
            end=datetime(2026, 10, 19, 16, 0, tzinfo=tz),
            new_status_code="DI1",
            tz=tz,
        )
        periods = request["staffMember"]["planningPeriods"]
        self.assertEqual(
            [period["state"]["code"] for period in periods],
            ["IND", "DI1", "AS1", "IND"],
        )
        self.assertEqual(
            (periods[2]["realStartTimeHour"], periods[2]["realStartTimeMinute"]),
            ("16", "0"),
        )

    def test_cycle_status_code(self) -> None:
        self.assertEqual(helpers.cycle_status_code("IND"), "DI1")
        self.assertEqual(helpers.cycle_status_code("DI1"), "AS1")
        self.assertEqual(helpers.cycle_status_code("AS1"), "IND")
        self.assertIsNone(helpers.cycle_status_code("GP"))

    def test_status_write_rejects_cross_row_range(self) -> None:
        tz = ZoneInfo("Europe/Paris")
        row = {
            "id": "ME",
            "planningId": 1,
            "planningStartDate": "2026-10-03",
            "planningPeriods": [
                {
                    "startTime": "00:00:00",
                    "endTime": "23:59:59",
                    "state": {"code": "AS1"},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "one planning row"):
            helpers.build_status_override_request(
                row,
                staff_id="ME",
                planning_start=helpers.parse_clock("07:00:00"),
                start=datetime(2026, 10, 3, 14, 38, tzinfo=tz),
                end=datetime(2026, 10, 5, 9, 0, tzinfo=tz),
                new_status_code="DI1",
                tz=tz,
            )


class OperationTests(unittest.TestCase):
    def test_structured_operation_snapshot(self) -> None:
        operation = {
            "id": 42,
            "number": "26000042",
            "dateCreation": "2026-09-30T11:43:00",
            "disasterLabel": "SECOURS A PERSONNE",
            "state": {"code": "EC", "name": "EN COURS"},
            "stationName": "CASERNE 08",
            "stationReceiverName": "CTA",
            "grpRadio": "G.04",
            "typeCode": "ITV",
            "personCount": 3,
            "address": {
                "city": "BEAUFORT",
                "streetNumber": "12",
                "streetCategory": "RUE",
                "streetName": "EXEMPLE",
                "cp": "39190",
                "latitude": 46.575123,
                "longitude": 5.438456,
            },
            "fireUnits": [
                {
                    "id": "08",
                    "name": "CASERNE 08",
                    "shortname": "CA08",
                    "state": {"code": "PA", "name": "PARTI"},
                }
            ],
            "vehicles": [
                {
                    "name": "VLTU 01",
                    "ownerFireUnit": {"id": "08"},
                    "state": {"code": "PA", "name": "PARTI"},
                }
            ],
            "externalServices": [
                {
                    "id": "POL",
                    "name": "POLICE",
                    "state": {"code": "ENG", "name": "ENGAGE"},
                }
            ],
        }
        data = helpers.operation_snapshot_data(operation)
        self.assertEqual(data["id"], "42")
        self.assertEqual(data["title"], "SECOURS A PERSONNE")
        self.assertEqual(data["state_code"], "EC")
        self.assertEqual(data["state_name"], "EN COURS")
        self.assertEqual(data["vehicles"][0]["center"], "CA08")
        self.assertEqual(data["type_code"], "ITV")
        self.assertEqual(data["resource_count"], 1)
        self.assertEqual(data["person_count"], 3)
        self.assertEqual(data["communication_group"], "G.04")
        self.assertEqual(data["station_name"], "CASERNE 08")
        self.assertEqual(data["station_receiver_name"], "CTA")
        self.assertEqual(data["centers"][0]["id"], "08")
        self.assertEqual(data["centers"][0]["name"], "CASERNE 08")
        self.assertEqual(data["centers"][0]["short_name"], "CA08")
        self.assertEqual(data["external_services"][0]["id"], "POL")
        self.assertEqual(data["external_services"][0]["state_code"], "ENG")
        self.assertEqual(data["history"], [])
        self.assertEqual(data["vehicles"][0]["name"], "VLTU 01")
        self.assertEqual(data["vehicles"][0]["state_code"], "PA")
        self.assertEqual(data["latitude"], 46.575123)
        self.assertEqual(data["longitude"], 5.438456)
        self.assertEqual(
            data["navigation_uri"],
            "deep-link://geo:0,0?q=46.575123%2C5.438456",
        )
        self.assertNotIn("message", data)
        self.assertNotIn("lifecycle", data)

    def test_operation_coordinates_scan_all_smartemis_addresses(self) -> None:
        latitude, longitude = helpers.operation_coordinates(
            {
                "addresses": (
                    {
                        "type": "IT",
                        "address": "SAINT-AMOUR - 22 RUE DE BRESSE",
                        "latitude": None,
                        "longitude": None,
                    },
                    {
                        "type": "GPS",
                        "address": "",
                        "latitude": "46.43625",
                        "longitude": "5.34492",
                    },
                )
            }
        )
        self.assertEqual(latitude, 46.43625)
        self.assertEqual(longitude, 5.34492)

    def test_operation_coordinates_from_navigation_url(self) -> None:
        latitude, longitude = helpers.operation_coordinates(
            {
                "url": (
                    "https://maps.example.invalid/?destination="
                    "46.43625%2C5.34492"
                )
            }
        )
        self.assertEqual(latitude, 46.43625)
        self.assertEqual(longitude, 5.34492)

    def test_navigation_url_coordinates_ignore_geo_zero_zero(self) -> None:
        latitude, longitude = helpers.navigation_url_coordinates(
            "geo:0,0?q=SAINT-AMOUR+-+22+RUE+DE+BRESSE"
        )
        self.assertIsNone(latitude)
        self.assertIsNone(longitude)

    def test_normalize_geocoding_query_reorders_artemis_address(self) -> None:
        self.assertEqual(
            helpers.normalize_geocoding_query(
                "SAINT-AMOUR - 22 RUE DE BRESSE"
            ),
            "22 RUE DE BRESSE SAINT-AMOUR",
        )

    def test_parse_geocoding_result_uses_geojson_coordinates(self) -> None:
        latitude, longitude, score, label = helpers.parse_geocoding_result(
            {
                "features": [
                    {
                        "geometry": {
                            "type": "Point",
                            "coordinates": [5.34321, 46.43654],
                        },
                        "properties": {
                            "score": 0.94,
                            "label": "22 Rue de Bresse 39160 Saint-Amour",
                        },
                    }
                ]
            }
        )
        self.assertEqual(latitude, 46.43654)
        self.assertEqual(longitude, 5.34321)
        self.assertEqual(score, 0.94)
        self.assertEqual(label, "22 Rue de Bresse 39160 Saint-Amour")

    def test_parse_geocoding_result_rejects_low_confidence(self) -> None:
        latitude, longitude, score, label = helpers.parse_geocoding_result(
            {
                "features": [
                    {
                        "geometry": {
                            "type": "Point",
                            "coordinates": [5.0, 46.0],
                        },
                        "properties": {
                            "score": 0.2,
                            "label": "Weak match",
                        },
                    }
                ]
            }
        )
        self.assertIsNone(latitude)
        self.assertIsNone(longitude)
        self.assertIsNone(score)
        self.assertEqual(label, "")

    def test_intervention_archive_tracks_new_update_and_end(self) -> None:
        records = {}
        operation = {
            "id": "42",
            "number": "26000042",
            "created": "2026-11-15T12:00:00+01:00",
            "title": "TEST",
            "latitude": 46.5,
            "longitude": 5.4,
            "type_code": "ITV",
        }

        changed, new = helpers.merge_intervention_archive(
            records,
            [operation],
            "2026-11-15T11:00:00+00:00",
        )
        key = helpers.intervention_archive_key(operation)
        self.assertEqual(changed, {key})
        self.assertEqual(new, {key})
        self.assertTrue(records[key]["active"])
        self.assertIsNone(records[key]["ended"])

        changed, new = helpers.merge_intervention_archive(
            records,
            [operation],
            "2026-11-15T11:01:00+00:00",
        )
        self.assertEqual(changed, set())
        self.assertEqual(new, set())

        changed, new = helpers.merge_intervention_archive(
            records,
            [],
            "2026-11-15T11:02:00+00:00",
        )
        self.assertEqual(changed, {key})
        self.assertEqual(new, set())
        self.assertFalse(records[key]["active"])
        self.assertEqual(records[key]["ended"], "2026-11-15T11:02:00+00:00")

    def test_navigation_falls_back_to_address_when_no_gps(self) -> None:
        operation = {
            "id": 44,
            "address": {
                "city": "BEAUFORT",
                "streetNumber": "12",
                "streetName": "EXEMPLE",
            },
        }
        data = helpers.operation_snapshot_data(operation)
        self.assertIsNone(data["latitude"])
        self.assertIsNone(data["longitude"])
        self.assertTrue(data["navigation_uri"].startswith("deep-link://geo:0,0?q="))
        self.assertIn("BEAUFORT", data["navigation_uri"])
        self.assertNotIn("package=", data["navigation_uri"])

    def test_geojson_point_coordinates(self) -> None:
        operation = {
            "id": 45,
            "geometry": {
                "type": "Point",
                "coordinates": [5.438456, 46.575123],
            },
        }
        data = helpers.operation_snapshot_data(operation)
        self.assertEqual(data["latitude"], 46.575123)
        self.assertEqual(data["longitude"], 5.438456)


if __name__ == "__main__":
    unittest.main()
