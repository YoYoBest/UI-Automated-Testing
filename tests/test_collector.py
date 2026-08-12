import json

import ei_ui_smoke.collector as collector


def test_collector_reads_details_and_atomically_builds_cache(tmp_path, monkeypatch):
    def fake_request(_page, method, url, **kwargs):
        if "list" in url:
            return {"data": {"records": [{"id": "1"}, {"id": "2"}]}}
        record_id = (kwargs.get("params") or {}).get("id")
        return {"data": {"phone": f"1390000000{record_id}", "amount": 100}}

    monkeypatch.setattr(collector, "browser_request", fake_request)
    output = tmp_path / "collected_data.json"
    result = collector.collect_form_data(
        object(),
        "FORM",
        {
            "list": {"method": "POST", "url": "/list", "recordsPath": "data.records"},
            "detail": {"method": "GET", "url": "/detail", "idParam": "id", "dataPath": "data"},
            "allowedFields": ["phone", "amount"],
        },
        output,
    )
    assert result["fields"]["phone"]["values"] == ["13900000001", "13900000002"]
    assert result["fields"]["amount"]["values"] == [100]
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["forms"]["FORM"]["sourceRecordIds"] == ["1", "2"]
    assert not output.with_suffix(".json.tmp").exists()

