"""
WATI tagging from /lapsed-customers: every tagged contact gets the remarketing
label and purchase = "false" in the same write, whichever API the tenant
serves.

WATI is replaced by a recorder, so nothing leaves the process:

    venv/bin/python -m unittest discover -s tests -v
"""
import json
import unittest

from app.services import wati

CUSTOMERS = [
    {"email": "ana@example.com", "name": "Ana", "phone": "+573001112233"},
    {"email": "luis@example.com", "name": "Luis", "phone": "3104792445"},
]

BOTH = [{"name": "remarketing", "value": "winback_oct"}, {"name": "purchase", "value": "false"}]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._body


class FakeWati:
    """Stands in for requests.Session: records every write and answers the v3
    update with v3_status / v3_body (or, call by call, v3_statuses) and the v1
    create with success."""

    def __init__(self, v3_status=200, v3_body=None, v3_statuses=()):
        self.v3_status = v3_status
        self.v3_body = {"contact_list": [{"id": "1"}]} if v3_body is None else v3_body
        self.v3_statuses = list(v3_statuses)
        self.writes = []

    def put(self, url, json=None, headers=None, timeout=None):
        self.writes.append(("v3", url, json))
        status = self.v3_statuses.pop(0) if self.v3_statuses else self.v3_status
        return FakeResponse(status, self.v3_body)

    def post(self, url, json=None, headers=None, timeout=None):
        self.writes.append(("v1", url, json))
        return FakeResponse(200, {"result": True})


def tag(fake):
    return wati.tag_contacts(tenant_url="https://live-mt-server.wati.io/123", api_token="test",
                             customers=CUSTOMERS, label="winback_oct", session=fake)


class PurchaseResetTests(unittest.TestCase):
    def test_v3_update_sets_the_label_and_resets_purchase(self):
        fake = FakeWati()
        result = tag(fake)
        self.assertEqual(result["tagged"], 2)
        self.assertEqual({w[0] for w in fake.writes}, {"v3"})
        for _api, _url, payload in fake.writes:
            self.assertEqual(payload["contacts"][0]["customParams"], BOTH)
        self.assertEqual((result["purchase_attribute"], result["purchase_value"]), ("purchase", "false"))

    def test_v1_tenant_gets_the_same_two_attributes(self):
        fake = FakeWati(v3_status=404, v3_body={})
        result = tag(fake)
        self.assertEqual(result["tagged"], 2)
        creates = [payload for api, _url, payload in fake.writes if api == "v1"]
        self.assertEqual(len(creates), 2)
        for payload in creates:
            self.assertEqual(payload["customParams"], BOTH)

    def test_contact_missing_in_wati_is_created_with_both(self):
        fake = FakeWati(v3_body={"contact_list": []})
        result = tag(fake)
        self.assertEqual(result["tagged"], 2)
        creates = [payload for api, _url, payload in fake.writes if api == "v1"]
        self.assertEqual([p["customParams"] for p in creates], [BOTH, BOTH])

    def test_a_later_v1_retry_writes_that_contact_not_the_first_again(self):
        # The first contact answers on v3; the second gets a 404 and must be
        # retried on v1 itself (it used to re-send the first contact instead).
        fake = FakeWati(v3_statuses=[200, 404])
        result = tag(fake)
        self.assertEqual(result["tagged"], 2)
        creates = [url for api, url, _payload in fake.writes if api == "v1"]
        self.assertEqual(len(creates), 1)
        self.assertTrue(creates[0].endswith("/addContact/573104792445"))


if __name__ == "__main__":
    unittest.main()
