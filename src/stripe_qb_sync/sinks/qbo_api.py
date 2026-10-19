"""Real QuickBooks Online adapter (reference implementation).

Not exercised by the offline test suite and never called by the demo. It is the drop-in
replacement for ``FakeQuickBooks`` — see README → "Going live".

Environment:
    QBO_CLIENT_ID, QBO_CLIENT_SECRET   from your Intuit developer app (Keys & credentials)
    QBO_REFRESH_TOKEN                  from the OAuth 2.0 Playground or your connect flow
    QBO_REALM_ID                       the company id of the sandbox company
    QBO_ENVIRONMENT                    "sandbox" (default) or "production"
"""

from __future__ import annotations

import os

from .base import DuplicateNameError

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
BASE_URLS = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
MINOR_VERSION = "75"
NAME_FIELD = {"Customer": "DisplayName", "Vendor": "DisplayName"}


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class QuickBooksOnlineSink:
    def __init__(self):
        import requests  # optional dependency: `pip install -e ".[live]"`

        self.http = requests.Session()
        self.realm = os.environ["QBO_REALM_ID"]
        self.base = BASE_URLS[os.environ.get("QBO_ENVIRONMENT", "sandbox")]
        self.refresh_token = os.environ["QBO_REFRESH_TOKEN"]
        self._refresh_access_token()
        self._customers_by_email: dict[str, dict] | None = None

    def _refresh_access_token(self) -> None:
        resp = self.http.post(
            TOKEN_URL,
            auth=(os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"]),
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        tokens = resp.json()
        # Intuit rotates refresh tokens; persist tokens["refresh_token"] in your secret store.
        self.refresh_token = tokens["refresh_token"]
        self.http.headers.update(
            {"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"}
        )

    def _url(self, path: str) -> str:
        return f"{self.base}/v3/company/{self.realm}/{path}"

    def _query(self, sql: str) -> list[dict]:
        resp = self.http.get(self._url("query"), params={"query": sql, "minorversion": MINOR_VERSION}, timeout=30)
        resp.raise_for_status()
        result = resp.json()["QueryResponse"]
        return next((v for k, v in result.items() if isinstance(v, list)), [])

    def find_by_name(self, entity: str, name: str) -> dict | None:
        field = NAME_FIELD.get(entity, "Name")
        rows = self._query(f"SELECT * FROM {entity} WHERE {field} = {_quote(name)}")
        return rows[0] if rows else None

    def find_customer_by_email(self, email: str) -> dict | None:
        # PrimaryEmailAddr is not a filterable query field, so page through customers once.
        if self._customers_by_email is None:
            self._customers_by_email, start = {}, 1
            while page := self._query(f"SELECT * FROM Customer STARTPOSITION {start} MAXRESULTS 1000"):
                for c in page:
                    addr = (c.get("PrimaryEmailAddr") or {}).get("Address")
                    if addr:
                        self._customers_by_email.setdefault(addr.strip().lower(), c)
                start += len(page)
        return self._customers_by_email.get(email.strip().lower())

    def find_invoice_by_doc_number(self, doc_number: str) -> dict | None:
        rows = self._query(f"SELECT * FROM Invoice WHERE DocNumber = {_quote(doc_number)}")
        return rows[0] if rows else None

    def get(self, entity: str, entity_id: str) -> dict | None:
        resp = self.http.get(self._url(f"{entity.lower()}/{entity_id}"), params={"minorversion": MINOR_VERSION}, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()[entity]

    def create(self, entity: str, body: dict, request_id: str) -> dict:
        resp = self.http.post(
            self._url(entity.lower()),
            params={"requestid": request_id, "minorversion": MINOR_VERSION},
            json=body,
            timeout=30,
        )
        if resp.status_code == 400:
            errors = resp.json().get("Fault", {}).get("Error", [])
            if any(e.get("code") == "6240" for e in errors):
                raise DuplicateNameError(errors[0].get("Detail", "Duplicate Name Exists Error"))
        resp.raise_for_status()
        obj = resp.json()[entity]
        if entity == "Customer" and self._customers_by_email is not None:
            addr = (obj.get("PrimaryEmailAddr") or {}).get("Address")
            if addr:
                self._customers_by_email[addr.strip().lower()] = obj
        return obj
