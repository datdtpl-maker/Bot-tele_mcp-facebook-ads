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
def count_active_campaigns(ad_account_id: str, limit: int = 100) -> dict[str, Any]:
    """Count active campaigns and return their names and ids."""
    return MetaAdsClient().count_active_campaigns(ad_account_id=ad_account_id, limit=limit)


@mcp.tool()
def find_campaign(ad_account_id: str, query: str, limit: int = 100) -> dict[str, Any]:
    """Find a campaign by id or partial name."""
    return MetaAdsClient().find_campaign(ad_account_id=ad_account_id, query=query, limit=limit)


@mcp.tool()
def campaign_budget_report(ad_account_id: str, limit: int = 50) -> dict[str, Any]:
    """Compare campaign/ad set budgets for campaigns in an ad account."""
    return MetaAdsClient().campaign_budget_report(ad_account_id=ad_account_id, limit=limit)


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
def preview_post_campaign(
    ad_account_id: str,
    name: str,
    goal: str,
    page_url: str,
    post_url: str,
) -> dict[str, Any]:
    """Preview a campaign from a Page URL and post URL, validating Page access first."""
    return MetaAdsClient().preview_post_campaign(
        ad_account_id=ad_account_id,
        name=name,
        goal=goal,
        page_url=page_url,
        post_url=post_url,
    )


@mcp.tool()
def preview_full_funnel(
    ad_account_id: str,
    name: str,
    goal: str,
    page_url: str,
    post_url: str,
    daily_budget: int,
    targeting: dict[str, Any],
    pixel_id: str | None = None,
    conversion_event: str | None = None,
) -> dict[str, Any]:
    """Preview campaign, ad set, post creative, and ad without changing Meta Ads."""
    return MetaAdsClient().preview_full_funnel(
        ad_account_id=ad_account_id,
        name=name,
        goal=goal,
        page_url=page_url,
        post_url=post_url,
        daily_budget=daily_budget,
        targeting=targeting,
        pixel_id=pixel_id,
        conversion_event=conversion_event,
    )


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
def create_full_funnel_paused(
    ad_account_id: str,
    name: str,
    goal: str,
    page_url: str,
    post_url: str,
    daily_budget: int,
    targeting: dict[str, Any],
    pixel_id: str | None = None,
    conversion_event: str | None = None,
) -> dict[str, Any]:
    """Create campaign, ad set, post creative, and ad, all paused."""
    return MetaAdsClient().create_full_funnel_paused(
        ad_account_id=ad_account_id,
        name=name,
        goal=goal,
        page_url=page_url,
        post_url=post_url,
        daily_budget=daily_budget,
        targeting=targeting,
        pixel_id=pixel_id,
        conversion_event=conversion_event,
    )


@mcp.tool()
def set_campaign_status(campaign_id: str, status: str) -> dict[str, Any]:
    """Set campaign status to ACTIVE or PAUSED."""
    return MetaAdsClient().set_campaign_status(campaign_id=campaign_id, status=status)


@mcp.tool()
def update_budget(
    object_id: str,
    object_type: str,
    budget: int,
    budget_type: str = "daily",
) -> dict[str, Any]:
    """Update daily or lifetime budget for a campaign or adset."""
    return MetaAdsClient().update_budget(
        object_id=object_id,
        object_type=object_type,
        budget=budget,
        budget_type=budget_type,
    )


if __name__ == "__main__":
    mcp.run()
