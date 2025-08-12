from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google_auth_oauthlib.flow import Flow
import os, yaml

app = FastAPI()

# ---------- CONFIG ----------
GOOGLE_ADS_YAML_PATH = "/etc/secrets/google-ads.yaml"
CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")  # ej: https://adsdash.onrender.com/oauth2/callback
SCOPES = ["https://www.googleapis.com/auth/adwords"]
# ----------------------------

def _client_config():
    if not (CLIENT_ID and CLIENT_SECRET and REDIRECT_URI):
        raise RuntimeError("Faltan GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI en Render.")
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }

def _ads_client() -> GoogleAdsClient:
    return GoogleAdsClient.load_from_storage(GOOGLE_ADS_YAML_PATH)

def _get_customer_id() -> str:
    with open(GOOGLE_ADS_YAML_PATH, "r") as fh:
        cfg = yaml.safe_load(fh) or {}
    cid = str(cfg.get("client_customer_id") or cfg.get("login_customer_id") or "").strip()
    if not cid:
        raise HTTPException(400, "No se encontró client_customer_id/login_customer_id en google-ads.yaml")
    return cid.replace("-", "")

# ---------- OAuth para obtener refresh_token ----------
@app.get("/oauth2/start")
def oauth2_start():
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)

@app.get("/oauth2/callback")
def oauth2_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(400, "Falta parámetro 'code'")
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return JSONResponse({
        "ok": True,
        "refresh_token": creds.refresh_token,
        "access_token": creds.token,
        "expiry": str(creds.expiry),
        "note": "Copia el refresh_token en /etc/secrets/google-ads.yaml y redeploy."
    })
# ------------------------------------------------------

@app.get("/ads/health")
def ads_health():
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
    customer_id: str | None = Query(None, description="Opcional: si no se pasa, usa el del YAML")
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
                "cost": r.metrics.cost_micros / 1_000_000  # moneda
            })
        return {"ok": True, "customer_id": cid, "rows": rows}
    except GoogleAdsException as gae:
        return {"ok": False, "error": gae.failure.message}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/")
def root():
    return {"ok": True, "service": "ads-api"}

@app.get("/ads/debug-config")
def ads_debug_config():
    import os, yaml
    p = "/etc/secrets/google-ads.yaml"
    exists = os.path.exists(p)
    data = {}
    if exists:
        with open(p,"r") as f:
            data = yaml.safe_load(f) or {}
    # No devuelvo el token completo por seguridad
    rt = data.get("refresh_token","")
    masked = (rt[:6] + "..." + rt[-6:]) if rt else ""
    return {
        "path_exists": exists,
        "keys_present": sorted(list(data.keys())) if data else [],
        "refresh_token_masked": masked
    }

