from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT = REPO_ROOT / "data" / "sample" / "input"


async def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=600) as client:
        async with app.router.lifespan_context(app):
            res = await client.post("/api/v1/shipments", json={"code": "LS2628", "customer_name": "U&I Logistics"})
            if res.status_code == 409:
                shipments = (await client.get("/api/v1/shipments")).json()
                shipment_id = next(s["id"] for s in shipments if s["code"] == "LS2628")
                print(f"reusing shipment {shipment_id}")
            else:
                shipment_id = res.json()["id"]
                print(f"created shipment {shipment_id}")

            files = [
                ("files", (p.name, p.read_bytes(), "application/octet-stream"))
                for p in sorted(INPUT.iterdir())
                if p.is_file()
            ]
            res = await client.post(f"/api/v1/shipments/{shipment_id}/documents", files=files)
            print("upload:", res.status_code)
            for row in res.json()["documents"]:
                print(f"  {row['status']:<9} {row.get('document_id','-')!s:<4} {row['filename'][:60]} {row.get('error','')}")

            res = await client.post(f"/api/v1/shipments/{shipment_id}/process", json={"force": True})
            job_id = res.json()["job_id"]
            print(f"job {job_id} started")

            for _ in range(240):
                await asyncio.sleep(2)
                job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
                print(f"  job {job['status']} attempts={job['attempts']}")
                if job["status"] in {"DONE", "FAILED"}:
                    break

            data = (await client.get(f"/api/v1/shipments/{shipment_id}/extractions")).json()
            print("\n== extraction results ==")
            for doc in data["documents"]:
                d = doc["document"]
                print(f"\n{d['status']:<12} {d['doc_type']:<28} {d['filename'][:52]}  ({d['doc_type_source']})")
                if d.get("error"):
                    print(f"   error: {d['error'][:160]}")
                for f in doc["fields"]:
                    if f["normalized_value"] is None:
                        continue
                    flag = "!" if f["critical"] else " "
                    print(
                        f"   {flag} {f['field_key']:<28} raw={str(f['raw_value'])[:34]:<34} norm={f['normalized_value']:<16} "
                        f"grounded={f['grounding_verified']} prov={f['provenance_type']}"
                    )
                for item in doc["line_items"]:
                    print(f"     line {item['seq']}: {item['description']} qty={item['quantity']} unit={item['unit']} amt={item['amount']}")

            parties = (await client.get(f"/api/v1/shipments/{shipment_id}/parties")).json()
            print("\n== parties ==")
            for p in parties:
                print(f"  {p['raw_name']} -> {p['roles']}")

            res = await client.post(f"/api/v1/shipments/{shipment_id}/exports", json={})
            print("\nexport:", res.status_code, res.json() if res.status_code < 300 else res.text[:400])


if __name__ == "__main__":
    asyncio.run(main())
