import importlib.util
import json
from pathlib import Path
import sys
import unittest

MODULE = Path(__file__).parents[1] / "custom_components" / "artemis" / "smartemis.py"
spec = importlib.util.spec_from_file_location("smartemis", MODULE)
smartemis = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["smartemis"] = smartemis
spec.loader.exec_module(smartemis)


class SmartemisProtocolTests(unittest.TestCase):
    def test_retrieve_sdis_request_matches_thrift_json_shape(self):
        payload = smartemis._request(
            "retrieveSdisList", {1: smartemis._scalar("str", "abc")}
        )
        self.assertEqual(
            payload,
            '[1,"retrieveSdisList",1,0,{"1":{"rec":{"1":{"str":"abc"}}}}]',
        )

    def test_get_sy_itv_request_matches_apk_struct(self):
        payload = smartemis._request(
            "getSyItv",
            {
                1: smartemis._scalar("str", "phone"),
                2: smartemis._scalar("str", "CS"),
                3: smartemis._scalar("str", "MAT"),
                4: smartemis._scalar("str", "null"),
            },
        )
        self.assertEqual(
            payload,
            '[1,"getSyItv",1,0,{"1":{"rec":{"1":{"str":"phone"},"2":{"str":"CS"},"3":{"str":"MAT"},"4":{"str":"null"}}}}]',
        )

    def test_decode_sdis_response(self):
        text = json.dumps(
            [
                1,
                "retrieveSdisList",
                2,
                0,
                {
                    "0": {
                        "rec": {
                            "1": {
                                "rec": {
                                    "1": {"str": "AM-OK"},
                                    "2": {"i32": 0},
                                }
                            },
                            "2": {
                                "lst": [
                                    "rec",
                                    1,
                                    {
                                        "1": {"str": "SDIS TEST"},
                                        "2": {
                                            "lst": [
                                                "str",
                                                1,
                                                "https://example.invalid/smartemis/",
                                            ]
                                        },
                                        "4": {"i32": 20},
                                        "5": {"tf": 1},
                                        "6": {"str": "fr"},
                                    },
                                ]
                            },
                        }
                    }
                },
            ]
        )
        response = smartemis._decode_response(text, "retrieveSdisList")
        smartemis._ensure_success(response)
        self.assertEqual(response[2][0][1], "SDIS TEST")
        self.assertEqual(
            response[2][0][2], ["https://example.invalid/smartemis/"]
        )
        self.assertTrue(response[2][0][5])

    def test_authentication_needed_is_allowed_for_registration(self):
        response = {1: {1: "AM-OK", 2: 1, 3: "Authentication required"}}
        result = smartemis._ensure_success(response, allow_auth_needed=True)
        self.assertEqual(result, smartemis.RESULT_AUTHENTICATION_NEEDED)

    def test_service_error_is_raised(self):
        response = {1: {1: "AM-REJ", 2: 2002, 3: "Unknown subscriber"}}
        with self.assertRaises(smartemis.SmartemisServiceError) as ctx:
            smartemis._ensure_success(response)
        self.assertEqual(ctx.exception.num, 2002)

    def test_default_tls_mode_matches_official_app(self):
        api = smartemis.SmartemisApi(object())
        self.assertFalse(api.verify_ssl)

    def test_center_rights_are_exposed(self):
        rights = smartemis._right_codes(
            [
                {1: "SMART:FCT:SY:INTV", 2: "Interventions"},
                {1: "SMART:FCT:PARAM", 2: "Paramètres"},
            ]
        )
        center = smartemis.SmartemisCenter(
            id="CS",
            name="Centre",
            matricule="MAT",
            person_id="P",
            status_code="IND",
            status_name="Indisponible",
            rights=rights,
        )
        self.assertTrue(center.can_view_interventions)
        self.assertIn(smartemis.RIGHT_PARAMETERS, center.rights)

    def test_user_context_parses_intervention_display_mode(self):
        context = smartemis._normalize_user_context(
            {
                1: "CS",
                2: "SP",
                3: "home",
                4: "O",
                5: "2",
                7: [{1: "Normal", 2: "1"}, {1: "Urgent", 2: "2"}],
            }
        )
        self.assertEqual(context.display_interventions, "O")
        self.assertEqual(context.display_level, "2")
        self.assertEqual(context.severity_levels[1], ("Urgent", "2"))

    def test_normalize_current_planning(self):
        response = {
            2: {1: "2026-11-10T10:00:00"},
            3: {
                1: {1: "DI1", 2: "DISPONIBLE NIV 1", 3: "#00ff00", 4: 1},
                2: {1: "2026-11-10T07:00:00"},
                3: {1: "2026-11-10T19:00:00"},
                4: 1,
            },
            4: [],
            5: [{1: "IND", 2: "INDISPONIBLE"}],
            6: {1: 4, 2: 8},
            7: 2,
        }
        result = smartemis._normalize_current_planning(response)
        self.assertEqual(result["current_period"]["status"]["code"], "DI1")
        self.assertEqual(result["available_personnel"], 4)
        self.assertEqual(result["max_personnel"], 8)
        self.assertEqual(result["allowed_statuses"][0]["code"], "IND")

    def test_normalize_intervention(self):
        result = smartemis._normalize_intervention(
            {
                1: 42,
                2: "26000042",
                3: "SECOURS A PERSONNE",
                4: "1 RUE EXEMPLE",
                5: {1: "2026-11-10T10:15:00"},
                6: "SAP",
                7: "https://example.invalid/itv/42",
                8: 2,
                9: 1,
                10: 4,
                11: "TALKGROUP",
            }
        )
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["title"], "SECOURS A PERSONNE")
        self.assertEqual(result["vehicle_count"], 2)
        self.assertEqual(result["created"], "2026-11-10T10:15:00")


class SmartemisApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_interventions_uses_global_view_like_apk(self):
        class FakeApi(smartemis.SmartemisApi):
            def __init__(self):
                self.calls = []

            async def async_get_user_context(
                self,
                url,
                *,
                phone_id,
                release_context=smartemis.SMARTEMIS_RELEASE_CONTEXT,
            ):
                return smartemis.SmartemisUserContext(
                    display_interventions="O",
                    display_level="CRIT2",
                )

            async def async_get_interventions(self, url, **kwargs):
                self.calls.append(kwargs)
                return ()

        api = FakeApi()
        center = smartemis.SmartemisCenter(
            id="CS01",
            name="Centre",
            matricule="MAT1",
            person_id="P1",
            status_code=None,
            status_name=None,
            rights=(smartemis.RIGHT_PARAMETERS, smartemis.RIGHT_INTERVENTIONS),
        )
        await api.async_get_interventions_for_center(
            "https://example.invalid/", phone_id="phone", center=center
        )
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(api.calls[0]["global_view"])
        self.assertEqual(api.calls[0]["display_level"], "CRIT2")

    async def test_auto_interventions_uses_center_when_global_mode_not_allowed(self):
        class FakeApi(smartemis.SmartemisApi):
            def __init__(self):
                self.calls = []

            async def async_get_user_context(
                self,
                url,
                *,
                phone_id,
                release_context=smartemis.SMARTEMIS_RELEASE_CONTEXT,
            ):
                return smartemis.SmartemisUserContext(
                    display_interventions="O",
                    display_level="CRIT2",
                )

            async def async_get_interventions(self, url, **kwargs):
                self.calls.append(kwargs)
                return ()

        api = FakeApi()
        center = smartemis.SmartemisCenter(
            id="CS01",
            name="Centre",
            matricule="MAT1",
            person_id="P1",
            status_code=None,
            status_name=None,
            rights=(smartemis.RIGHT_INTERVENTIONS,),
        )
        await api.async_get_interventions_for_center(
            "https://example.invalid/", phone_id="phone", center=center
        )
        self.assertFalse(api.calls[0]["global_view"])


if __name__ == "__main__":
    unittest.main()
