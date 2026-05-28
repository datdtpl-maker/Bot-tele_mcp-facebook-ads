from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from analytics import analyze_insights
from meta_ads_client import MetaAdsClient


load_dotenv()
mcp = FastMCP("meta-ads-mcp")


@mcp.tool()
def list_ad_accounts(limit: int = 25) -> dict[str, Any]:
    """List Meta ad accounts available to the configured access token."""
    return MetaAdsClient().list_ad_accounts(limit=limit)


@mcp.tool()
def list_campaigns(ad_account_id: str, limit: int = 25) -> dict[str, Any]:
    """List campaigns for an ad account. ad_account_id must look like act_123."""
    return MetaAdsClient().list_campaigns(ad_account_id=ad_account_id, limit=limit)


@mcp.tool()
def list_adsets(campaign_id: str, limit: int = 25) -> dict[str, Any]:
    """List ad sets for a campaign."""
    return MetaAdsClient().list_adsets(campaign_id=campaign_id, limit=limit)


@mcp.tool()
def list_ads(parent_id: str, limit: int = 25) -> dict[str, Any]:
    """List ads for a campaign or ad set id."""
    return MetaAdsClient().list_ads(parent_id=parent_id, limit=limit)


@mcp.tool()
def get_status(object_type: str, object_id: str) -> dict[str, Any]:
    """Get status for a campaign, adset, or ad."""
    return MetaAdsClient().get_status(object_id=object_id, object_type=object_type)


@mcp.tool()
def get_insights(
    ad_account_id: str,
    since: str,
    until: str,
    level: str = "campaign",
    limit: int = 25,
) -> dict[str, Any]:
    """Get Meta Ads insights for an account and date range, grouped by level."""
    return MetaAdsClient().get_insights(
        ad_account_id=ad_account_id,
        since=since,
        until=until,
        level=level,
        limit=limit,
    )


@mcp.tool()
def analyze_performance(
    ad_account_id: str,
    since: str,
    until: str,
    level: str = "campaign",
    goal: str = "conversions",
    limit: int = 100,
) -> dict[str, Any]:
    """Analyze ads insights and return summary, winners, losers, and best result."""
    insights = MetaAdsClient().get_insights(
        ad_account_id=ad_account_id,
        since=since,
        until=until,
        level=level,
        limit=limit,
    )
    return analyze_insights(insights.get("data", []), goal=goal)


@mcp.tool()
def preview_campaign(ad_account_id: str, name: str, goal: str = "conversions") -> dict[str, Any]:
    """Preview a messages/conversions/leads/traffic campaign without changing Meta Ads."""
    return MetaAdsClient().preview_campaign(ad_account_id=ad_account_id, name=name, goal=goal)


@mcp.tool()
def create_campaign_paused(
    ad_account_id: str,
    name: str,
    objective: str = "OUTCOME_TRAFFIC",
) -> dict[str, Any]:
    """Create a paused campaign draft. It will not spend until explicitly activated."""
    return MetaAdsClient().create_campaign_paused(
        ad_account_id=ad_account_id,
        name=name,
        objective=objective,
    )


@mcp.tool()
def create_campaign_for_goal(
    ad_account_id: str,
    name: str,
    goal: str = "conversions",
) -> dict[str, Any]:
    """Create a paused campaign for messages/conversions/leads/traffic."""
    return MetaAdsClient().create_campaign_for_goal(ad_account_id=ad_account_id, name=name, goal=goal)


@mcp.tool()
def set_campaign_status(campaign_id: str, status: str) -> dict[str, Any]:
    """Set campaign status to ACTIVE or PAUSED."""
    return MetaAdsClient().set_campaign_status(campaign_id=campaign_id, status=status)


if __name__ == "__main__":
    mcp.run()
