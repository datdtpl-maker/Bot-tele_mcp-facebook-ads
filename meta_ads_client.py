import os
from typing import Any

import requests


CAMPAIGN_OBJECTIVES = {
    "messages": "OUTCOME_ENGAGEMENT",
    "conversions": "OUTCOME_SALES",
    "traffic": "OUTCOME_TRAFFIC",
    "leads": "OUTCOME_LEADS",
}


class MetaAdsError(RuntimeError):
    pass


class MetaAdsClient:
    def __init__(self, access_token: str | None = None, api_version: str | None = None) -> None:
        self.access_token = access_token or os.environ["META_ACCESS_TOKEN"]
        self.api_version = api_version or os.getenv("META_GRAPH_API_VERSION", "v22.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def _request(self, method: str, path: str, **params: Any) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        payload = {k: v for k, v in params.items() if v is not None}
        payload["access_token"] = self.access_token

        if method.upper() == "GET":
            response = requests.get(url, params=payload, timeout=30)
        else:
            response = requests.post(url, data=payload, timeout=30)

        try:
            data = response.json()
        except ValueError as exc:
            raise MetaAdsError(f"Meta API returned non-JSON response: {response.text[:300]}") from exc

        if response.status_code >= 400 or "error" in data:
            error = data.get("error", data)
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise MetaAdsError(message)

        return data

    def list_ad_accounts(self, limit: int = 25) -> dict[str, Any]:
        return self._request(
            "GET",
            "me/adaccounts",
            fields="id,name,account_status,currency,timezone_name",
            limit=limit,
        )

    def list_campaigns(self, ad_account_id: str, limit: int = 25) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{ad_account_id}/campaigns",
            fields="id,name,status,effective_status,objective,created_time,updated_time",
            limit=limit,
        )

    def list_adsets(self, campaign_id: str, limit: int = 25) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{campaign_id}/adsets",
            fields="id,name,status,effective_status,campaign_id,daily_budget,lifetime_budget,optimization_goal,billing_event,created_time,updated_time",
            limit=limit,
        )

    def list_ads(self, parent_id: str, limit: int = 25) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{parent_id}/ads",
            fields="id,name,status,effective_status,campaign_id,adset_id,created_time,updated_time",
            limit=limit,
        )

    def get_status(self, object_id: str, object_type: str) -> dict[str, Any]:
        fields_by_type = {
            "campaign": "id,name,status,effective_status,objective,created_time,updated_time",
            "adset": "id,name,status,effective_status,campaign_id,daily_budget,lifetime_budget,optimization_goal,created_time,updated_time",
            "ad": "id,name,status,effective_status,campaign_id,adset_id,created_time,updated_time",
        }
        if object_type not in fields_by_type:
            raise MetaAdsError("object_type must be campaign, adset, or ad")
        return self._request("GET", object_id, fields=fields_by_type[object_type])

    def get_insights(
        self,
        ad_account_id: str,
        since: str,
        until: str,
        level: str = "campaign",
        limit: int = 25,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{ad_account_id}/insights",
            level=level,
            time_range=f'{{"since":"{since}","until":"{until}"}}',
            fields="campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,clicks,ctr,cpc,cpm,actions,action_values",
            limit=limit,
        )

    def preview_campaign(self, ad_account_id: str, name: str, goal: str) -> dict[str, Any]:
        objective = CAMPAIGN_OBJECTIVES.get(goal, goal)
        return {
            "dry_run": True,
            "ad_account_id": ad_account_id,
            "name": name,
            "goal": goal,
            "objective": objective,
            "status": "PAUSED",
            "note": "Preview only. No Meta API mutation was sent.",
        }

    def create_campaign_paused(
        self,
        ad_account_id: str,
        name: str,
        objective: str,
        special_ad_categories: list[str] | None = None,
    ) -> dict[str, Any]:
        categories = special_ad_categories or ["NONE"]
        return self._request(
            "POST",
            f"{ad_account_id}/campaigns",
            name=name,
            objective=objective,
            status="PAUSED",
            special_ad_categories=str(categories).replace("'", '"'),
        )

    def create_campaign_for_goal(self, ad_account_id: str, name: str, goal: str) -> dict[str, Any]:
        objective = CAMPAIGN_OBJECTIVES.get(goal, goal)
        return self.create_campaign_paused(ad_account_id=ad_account_id, name=name, objective=objective)

    def set_campaign_status(self, campaign_id: str, status: str) -> dict[str, Any]:
        if status not in {"ACTIVE", "PAUSED"}:
            raise MetaAdsError("status must be ACTIVE or PAUSED")
        return self._request("POST", campaign_id, status=status)
