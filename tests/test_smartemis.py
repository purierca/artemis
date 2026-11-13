import importlib.util
import sys
import types
import unittest
from pathlib import Path

if importlib.util.find_spec("aiohttp") is None:
    aiohttp_stub = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, *, total=None):
            self.total = total

    aiohttp_stub.ClientError = ClientError
    aiohttp_stub.ClientTimeout = ClientTimeout
    aiohttp_stub.ClientSession = object
    sys.modules["aiohttp"] = aiohttp_stub

MODULE = Path(__file__).parents[1] / "custom_components" / "artemis" / "smartemis.py"
spec = importlib.util.spec_from_file_location("smartemis", MODULE)
smartemis = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["smartemis"] = smartemis
spec.loader.exec_module(smartemis)


def _center(center_id: str, person_status: str):
    return smartemis.SmartemisCenter(
        id=center_id,
        name=f"CASERNE {center_id}",
        matricule="MAT",
        person_id="PERSON",
        last_name="",
        first_name="",
        person_status_code=person_status,
        administrative_status_code=None,
        administrative_status_name=None,
        rights=(),
    )


class SmartemisProtocolTests(unittest.TestCase):
    def test_request_shapes_match_apk_thrift_structs(self):
        cases = {
            "retrieveSdisList": (
                {1: smartemis._scalar("str", "phone")},
                '[1,"retrieveSdisList",1,0,{"1":{"rec":{"1":{"str":"phone"}}}}]',
            ),
            "getCurrentPlanning": (
                {
                    1: smartemis._scalar("str", "phone"),
                    2: smartemis._scalar("str", "05"),
                    3: smartemis._scalar("str", "MAT"),
                },
                '[1,"getCurrentPlanning",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"str":"05"},"3":{"str":"MAT"}}}}]',
            ),
            "getSyItv": (
                {
                    1: smartemis._scalar("str", "phone"),
                    2: smartemis._scalar("str", "null"),
                    3: smartemis._scalar("str", "null"),
                    4: smartemis._scalar("str", "2"),
                },
                '[1,"getSyItv",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"str":"null"},"3":{"str":"null"},"4":{"str":"2"}}}}]',
            ),
            "getItv": (
                {
                    1: smartemis._scalar("str", "phone"),
                    2: smartemis._scalar("str", "05"),
                    3: smartemis._scalar("str", "MAT"),
                    4: smartemis._scalar("i32", 42),
                },
                '[1,"getItv",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"str":"05"},"3":{"str":"MAT"},"4":{"i32":42}}}}]',
            ),
        }
        for method, (fields, expected) in cases.items():
            with self.subTest(method=method):
                self.assertEqual(smartemis._request(method, fields), expected)

    def test_get_subscriber_request_matches_apk_struct(self):
        payload = smartemis._request(
            "getSubscriber",
            {
                1: smartemis._scalar("str", "phone"),
                2: smartemis._scalar("tf", 1),
                3: smartemis._scalar("tf", 1),
                4: smartemis._scalar("tf", 1),
            },
        )
        self.assertEqual(
            payload,
            '[1,"getSubscriber",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"tf":1},"3":{"tf":1},"4":{"tf":1}}}}]',
        )

    def test_subscriber_normalization_reuses_existing_identity(self):
        subscriber = smartemis._normalize_subscriber(
            {
                1: "existing-phone-id",
                2: "PERSON",
                3: "TEST SERVICE",
                4: True,
            }
        )
        self.assertEqual(subscriber.phone_id, "existing-phone-id")
        self.assertEqual(subscriber.person_id, "PERSON")
        self.assertEqual(subscriber.organization, "TEST SERVICE")

    def test_update_current_planning_eop_request_matches_apk_struct(self):
        payload = smartemis._request(
            "updateCurrentPlanning",
            smartemis._update_current_planning_fields(
                phone_id="phone",
                center_id="05",
                matricule="MAT",
                status={
                    "code": "DI1",
                    "name": "DISPONIBLE NIV 1",
                    "rgb": "00FF00",
                    "order": 2,
                },
                period_end="2026-11-11T19:00:00.0+0100",
            ),
        )
        self.assertEqual(
            payload,
            '[1,"updateCurrentPlanning",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"str":"05"},"3":{"rec":{"1":{"rec":{"1":{"str":"DI1"},"2":{"str":"DISPONIBLE NIV 1"},"3":{"str":"00FF00"},"4":{"i32":2}}},"3":{"rec":{"1":{"str":"2026-11-11T19:00:00.0+0100"}}}}},"4":{"rec":{"1":{"str":"EOP"}}},"5":{"str":"MAT"}}}}]',
        )

    def test_update_current_planning_requires_status_and_boundary(self):
        with self.assertRaises(ValueError):
            smartemis._update_current_planning_fields(
                phone_id="phone",
                center_id="05",
                matricule="MAT",
                status={},
                period_end="2026-11-11T19:00:00.0+0100",
            )
        with self.assertRaises(ValueError):
            smartemis._update_current_planning_fields(
                phone_id="phone",
                center_id="05",
                matricule="MAT",
                status={"code": "DI1"},
                period_end="",
            )

    def test_service_error_is_raised(self):
        with self.assertRaises(smartemis.SmartemisServiceError) as ctx:
            smartemis._ensure_success(
                {1: {1: "UNKNOWN_SUBSCRIBER", 2: 2002, 3: "Unknown subscriber"}}
            )
        self.assertEqual(ctx.exception.num, 2002)

    def test_default_tls_mode_matches_official_app(self):
        self.assertFalse(smartemis.SmartemisApi(object())._verify_ssl)

    def test_center_decodes_person_and_administrative_status(self):
        center = smartemis._normalize_center(
            {
                1: {
                    1: "05",
                    2: "CASERNE 05",
                    3: "X",
                    5: "MAT",
                    6: "PERSON",
                    7: "DOE",
                    8: "JOHN",
                    9: {1: "DI1", 2: "DISPONIBLE NIV 1"},
                },
                2: [{1: smartemis.RIGHT_PARAMETERS}],
            }
        )
        self.assertEqual(center.id, "05")
        self.assertEqual(center.person_name, "JOHN DOE")
        self.assertEqual(center.person_status_code, "X")
        self.assertEqual(center.administrative_status_code, "DI1")
        self.assertIn(smartemis.RIGHT_PARAMETERS, center.rights)

    def test_user_context_decodes_intervention_settings(self):
        context = smartemis._normalize_user_context({1: "05", 4: "O", 5: "CRIT2"})
        self.assertEqual(context.default_center_code, "05")
        self.assertEqual(context.display_interventions, "O")
        self.assertEqual(context.display_level, "CRIT2")

    def test_primary_center_uses_affiliation_not_default_display_center(self):
        centers = (_center("02", "N"), _center("05", "X"), _center("30", "N"))
        selected = smartemis.select_primary_center(
            centers, smartemis.SmartemisUserContext(default_center_code="30")
        )
        self.assertEqual(selected.id, "05")

    def test_primary_center_falls_back_to_single_affiliated_profile(self):
        centers = (_center("02", "N"), _center("05", "X"), _center("08", "N"))
        selected = smartemis.select_primary_center(
            centers, smartemis.SmartemisUserContext()
        )
        self.assertEqual(selected.id, "05")

    def test_primary_center_rejects_unaffiliated_only_profiles(self):
        centers = (_center("02", "N"), _center("30", "N"))
        with self.assertRaises(smartemis.SmartemisServiceError) as ctx:
            smartemis.select_primary_center(
                centers, smartemis.SmartemisUserContext(default_center_code="30")
            )
        self.assertEqual(ctx.exception.code, "NO_AFFILIATED_CENTER")

    def test_current_planning_normalization(self):
        result = smartemis._normalize_current_planning(
            {
                3: {
                    1: {1: "DI1", 2: "DISPONIBLE NIV 1"},
                    2: {1: "2026-11-10T07:00:00.0+0100"},
                    3: {1: "2026-11-10T19:00:00.0+0100"},
                },
                4: [],
                5: [{1: "IND", 2: "INDISPONIBLE"}],
                6: {1: 4, 2: 8},
            }
        )
        self.assertEqual(result["current_period"]["status"]["code"], "DI1")
        self.assertEqual(result["allowed_statuses"][0]["code"], "IND")
        self.assertEqual(result["available_personnel"], 4)

    def test_intervention_detail_normalization(self):
        result = smartemis._normalize_intervention_detail(
            {
                2: {
                    1: [
                        {
                            3: "78",
                            4: "CA78",
                            5: [{3: 278, 4: "278", 5: "RDC"}],
                        }
                    ],
                    3: [{1: "IT", 2: "ADDRESS", 3: "-73.5", 4: "45.5"}],
                }
            }
        )
        self.assertEqual(result["vehicles"][0]["center_name"], "CA78")
        self.assertEqual(result["vehicles"][0]["state_code"], "RDC")
        self.assertEqual(result["addresses"][0]["longitude"], "-73.5")
        self.assertEqual(result["addresses"][0]["latitude"], "45.5")


class SmartemisWriteClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_current_planning_calls_eop_and_decodes_response(self):
        class FakeResponse:
            status = 200

            async def text(self):
                return (
                    '[1,"updateCurrentPlanning",2,0,{"0":{"rec":{'
                    '"1":{"rec":{"1":{"str":"OK"},"2":{"i32":0}}},'
                    '"3":{"rec":{"1":{"rec":{"1":{"str":"DI1"},'
                    '"2":{"str":"DISPONIBLE NIV 1"}}},'
                    '"3":{"rec":{"1":{"str":"2026-11-11T19:00:00.0+0100"}}}}'
                    '}}}}]'
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def __init__(self):
                self.payload = None

            def post(self, url, **kwargs):
                self.payload = kwargs["data"]
                return FakeResponse()

        session = FakeSession()
        api = smartemis.SmartemisApi(session)
        phone_id = "super-secret-phone-id"
        with self.assertLogs("smartemis", level="DEBUG") as logs:
            result = await api.async_update_current_planning(
                "https://example.invalid/",
                phone_id=phone_id,
                center_id="05",
                matricule="MAT",
                status={
                    "code": "DI1",
                    "name": "DISPONIBLE NIV 1",
                    "rgb": "00FF00",
                    "order": 2,
                },
                period_end="2026-11-11T19:00:00.0+0100",
            )

        joined_logs = "\n".join(logs.output)
        self.assertNotIn(phone_id, joined_logs)
        self.assertIn("updateCurrentPlanning", joined_logs)
        self.assertIn('"updateCurrentPlanning"', session.payload)
        self.assertIn('"EOP"', session.payload)
        self.assertEqual(result["current_period"]["status"]["code"], "DI1")


if __name__ == "__main__":
    unittest.main()
