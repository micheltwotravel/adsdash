from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import yaml

app = FastAPI()

GOOGLE_ADS_YAML_PATH = "/etc/secrets/google-ads.yaml"

def _ads_client() -> GoogleAdsClient:
    return GoogleAdsClient.load_from_storage(GOOGLE_ADS_YAML_PATH)

def _get_customer_id() -> str:
    """Lee el customer_id por defecto del YAML."""
    with open(GOOGLE_ADS_YAML_PATH, "r") as fh:
        cfg = yaml.safe_load(fh) or {}
    cid = str(cfg.get("client_customer_id") or cfg.get("login_customer_id") or "").strip()
    if not cid:
        raise HTTPException(400, "No se encontró client_customer_id/login_customer_id en google-ads.yaml")
    return cid.replace("-", "")

@app.get("/ads/health")
def ads_health():
    """Verifica conexión y devuelve lista de cuentas accesibles."""
    try:
        client = _ads_client()
        cust_svc = client.get_service("CustomerService")
        res = cust_svc.list_accessible_customers()
        return {"ok": True, "customers": list(res.resource_names)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/ads/campaigns")
def ads_campaigns(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    customer_id: str = Query(None, description="Opcional: si no se pasa, usa el del YAML")
):
    try:
        client = _ads_client()
        ga_service = client.get_service("GoogleAdsService")
        cid = customer_id.replace("-", "") if customer_id else _get_customer_id()

        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros
            FROM campaign
            WHERE segments.date BETWEEN '{start}' AND '{end}'
            ORDER BY campaign.id
            LIMIT 100
        """

        rows = []
        response = ga_service.search(customer_id=cid, query=query)
        for r in response:
            rows.append({
                "campaign_id": r.campaign.id,
                "campaign_name": r.campaign.name,
                "impressions": r.metrics.impressions,
                "clicks": r.metrics.clicks,
                "cost_micros": r.metrics.cost_micros / 1_000_000  # convertir a moneda
            })
        return {"ok": True, "customer_id": cid, "rows": rows}
    except GoogleAdsException as gae:
        return {"ok": False, "error": gae.failure.message}
    except Exception as e:
        return {"ok": False, "error": str(e)}
