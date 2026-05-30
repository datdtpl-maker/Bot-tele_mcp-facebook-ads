import os
import re
from typing import Any
from urllib.parse import urlparse

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
            fields="id,name,status,effective_status,objective,daily_budget,lifetime_budget,created_time,updated_time",
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

    def find_campaign(self, ad_account_id: str, query: str, limit: int = 100) -> dict[str, Any]:
        campaigns = self.list_campaigns(ad_account_id=ad_account_id, limit=limit).get("data", [])
        exact = [item for item in campaigns if item.get("id") == query]
        if exact:
            return {"match": exact[0], "candidates": exact}
        query_lower = query.lower()
        candidates = [item for item in campaigns if query_lower in item.get("name", "").lower()]
        return {"match": candidates[0] if len(candidates) == 1 else None, "candidates": candidates[:10]}

    def count_active_campaigns(self, ad_account_id: str, limit: int = 100) -> dict[str, Any]:
        campaigns = self.list_campaigns(ad_account_id=ad_account_id, limit=limit).get("data", [])
        active = [
            item
            for item in campaigns
            if item.get("status") == "ACTIVE" or item.get("effective_status") == "ACTIVE"
        ]
        return {"count": len(active), "campaigns": active, "total_checked": len(campaigns)}

    def campaign_budget_report(self, ad_account_id: str, limit: int = 50) -> dict[str, Any]:
        campaigns = self.list_campaigns(ad_account_id=ad_account_id, limit=limit).get("data", [])
        rows = []
        for campaign in campaigns:
            adsets = self.list_adsets(campaign["id"], limit=100).get("data", [])
            daily_budget = sum(int(item.get("daily_budget") or 0) for item in adsets)
            lifetime_budget = sum(int(item.get("lifetime_budget") or 0) for item in adsets)
            rows.append(
                {
                    "campaign_id": campaign.get("id"),
                    "campaign_name": campaign.get("name"),
                    "status": campaign.get("status"),
                    "effective_status": campaign.get("effective_status"),
                    "campaign_daily_budget": int(campaign.get("daily_budget") or 0),
                    "campaign_lifetime_budget": int(campaign.get("lifetime_budget") or 0),
                    "adset_daily_budget": daily_budget,
                    "adset_lifetime_budget": lifetime_budget,
                    "adset_count": len(adsets),
                }
            )
        rows.sort(key=lambda item: item["campaign_daily_budget"] + item["adset_daily_budget"], reverse=True)
        return {"data": rows}

    def validate_page_access(self, page_url: str) -> dict[str, Any]:
        page_ref = self._page_ref_from_url(page_url)
        page = self._request("GET", page_ref, fields="id,name,link")
        return {
            "ok": True,
            "page_id": page.get("id"),
            "page_name": page.get("name"),
            "page_link": page.get("link", page_url),
        }

    def validate_ad_account_page_access(self, ad_account_id: str, page_url: str) -> dict[str, Any]:
        page = self.validate_page_access(page_url)
        promote_pages = self._request(
            "GET",
            f"{ad_account_id}/promote_pages",
            fields="id,name,link",
            limit=100,
        ).get("data", [])
        matched = [item for item in promote_pages if item.get("id") == page["page_id"]]
        if not matched:
            raise MetaAdsError(
                "The configured ad account/token can read the Page, but the Page is not returned in this ad account's promote_pages list."
            )
        page["ad_account_can_promote_page"] = True
        return page

    def preview_post_campaign(
        self,
        ad_account_id: str,
        name: str,
        goal: str,
        page_url: str,
        post_url: str,
    ) -> dict[str, Any]:
        page = self.validate_ad_account_page_access(ad_account_id=ad_account_id, page_url=page_url)
        preview = self.preview_campaign(ad_account_id=ad_account_id, name=name, goal=goal)
        preview.update(
            {
                "page_access": page,
                "post_url": post_url,
                "required_next_fields_for_live_ad": [
                    "daily_budget",
                    "targeting",
                    "billing_event",
                    "optimization_goal",
                    "pixel_id and conversion_event for conversions",
                    "creative/ad creation from post_id",
                ],
            }
        )
        return preview

    def _page_ref_from_url(self, page_url: str) -> str:
        parsed = urlparse(page_url)
        if not parsed.netloc:
            return page_url
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise MetaAdsError("page_url must contain a Page username or id")
        if parts[0] == "profile.php":
            match = re.search(r"(?:^|[?&])id=([^&]+)", parsed.query)
            if match:
                return match.group(1)
        return parts[0]

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
