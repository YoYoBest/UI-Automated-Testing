from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


class RuntimeApi:
    """Calls UIM through the authenticated browser context; tenantId is never sent."""

    def __init__(self, page: "Page", api_prefix: str = "/ezgo/ezgo-uim"):
        self.page = page
        self.api_prefix = api_prefix.rstrip("/")

    def _request(self, method: str, path: str, data: dict[str, Any]) -> Any:
        assert "tenantId" not in data, "tenantId must come from request context"
        return self.page.evaluate(
            """async ({method, url, data}) => {
              const headers = {'Content-Type': 'application/json'};
              const token = localStorage.getItem('accessToken');
              const tenant = localStorage.getItem('tenantId') || localStorage.getItem('tenant-id');
              if (token) headers.Authorization = token;
              if (tenant) {
                headers.tenantId = tenant;
                headers['x-tenant-id'] = tenant;
                headers['X-Tenant-Id'] = tenant;
              }
              headers['X-Language'] = localStorage.getItem('i18n-language') || 'zh_CN';
              const query = method === 'GET' ? '?' + new URLSearchParams(data).toString() : '';
              const response = await fetch(url + query, {
                method, headers, body: method === 'GET' ? undefined : JSON.stringify(data)
              });
              const text = await response.text();
              let body; try { body = JSON.parse(text); } catch { body = text; }
              if (!response.ok) throw new Error(`${response.status} ${text}`);
              return body;
            }""",
            {"method": method, "url": f"{self.api_prefix}{path}", "data": data},
        )

    def _post(self, path: str, data: dict[str, Any]) -> Any:
        return self._request("POST", path, data)

    def get_form_config(self, form_code: str) -> Any:
        return self._post("/tenantForm/getFormLastVersion", {"formCode": form_code})

    def get_runtime_list_config(self, form_code: str) -> Any:
        return self._post("/tenantForm/list/config", {"formCode": form_code})

    def get_form_data(self, form_code: str, business_id: str) -> Any:
        return self._request("GET", "/tenantForm/getFormData", {"formCode": form_code, "businessId": business_id})

    def save_form_data(self, form_code: str, business_id: str, data_json: str, data_key: str | None = None) -> Any:
        payload = {"formCode": form_code, "businessId": business_id, "dataJson": data_json}
        if data_key:
            payload["dataKey"] = data_key
        return self._post("/tenantForm/saveFormData", payload)

    def update_form_data(self, form_code: str, business_id: str, data_json: str, data_key: str | None = None) -> Any:
        payload = {"formCode": form_code, "businessId": business_id, "dataJson": data_json}
        if data_key:
            payload["dataKey"] = data_key
        return self._post("/tenantForm/updateFormData", payload)
