from app.config import settings

#Looks like this is going to be the research tool - CHECK!
def search_company(name: str, country: str | None = None) -> list[dict]:
    # Search results are unverified research leads, not validated facts.
    if not settings.research_enabled or not settings.tavily_api_key:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=settings.tavily_api_key)
        query = f'"{name}" {country or ""} financial results supplier risk delivery news'.strip()
        response = client.search(query=query, max_results=5, search_depth="basic")
        return [{
            "title": x.get("title", ""), "url": x.get("url", ""),
            "content": (x.get("content") or "")[:2500],
            "source_type": "unverified_external_research"
        } for x in response.get("results", [])]
    except Exception:
        return []
