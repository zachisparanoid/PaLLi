"""
Web search through Exa.ai. Her web_search tool and her curiosity research
both reach Exa through `web_search_engine.exa_client`
(core/agent/builtin_tools/web_search.py, ai/curiosity_research.py).

The old decide-then-summarize path (should_search_web, perform_search and
their web_search_decision / web_search_summarization prompts) had no caller
left and was deleted in Batch 8 (2026-10-01); the prompts are in
prompts/archive/.
"""

import logging
from typing import Dict, List, Optional

from core.config import config

logger = logging.getLogger(__name__)


class ExaSearchClient:
    """Client for Exa.ai search API"""

    def __init__(self):
        self.api_key = config.EXA_API_KEY
        self.base_url = "https://api.exa.ai"
        self.session = None

    async def _get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            import aiohttp
            headers = {
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
            }
            self.session = aiohttp.ClientSession(headers=headers)
        return self.session

    async def search(
        self,
        query: str,
        num_results: int = 10,
        include_domains: List[str] = None,
        exclude_domains: List[str] = None,
        start_crawl_date: str = None,
        end_crawl_date: str = None,
        start_published_date: str = None,
        end_published_date: str = None,
        use_autoprompt: bool = True,
        type: str = "auto",
        summary: bool = True,
        livecrawl: str = "always",
    ) -> Optional[Dict]:
        """Enhanced search with comprehensive parameters"""
        try:
            session = await self._get_session()

            payload = {
                "query": query,
                "numResults": num_results,
                "useAutoprompt": use_autoprompt,
                "type": type,
                "livecrawl": livecrawl,
                "contents": {
                    "text": True, 
                    "highlights": False, 
                    "summary": summary
                }
            }

            # Add optional parameters if provided
            if include_domains:
                payload["includeDomains"] = include_domains
            if exclude_domains:
                payload["excludeDomains"] = exclude_domains
            if start_crawl_date:
                payload["startCrawlDate"] = start_crawl_date
            if end_crawl_date:
                payload["endCrawlDate"] = end_crawl_date
            if start_published_date:
                payload["startPublishedDate"] = start_published_date
            if end_published_date:
                payload["endPublishedDate"] = end_published_date

            async with session.post(
                f"{self.base_url}/search",
                json=payload,
                timeout=45,
            ) as response:

                if response.status == 200:
                    result = await response.json()
                    logger.debug(f"Exa search successful: {len(result.get('results', []))} results with content")
                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"Exa search error {response.status}: {error_text}")
                    return None

        except Exception as e:
            logger.error(f"Exa search error: {e}")
            return None

    async def search_and_contents(
        self, query: str, num_results: int = 5, use_autoprompt: bool = True, type: str = "neural"
    ) -> Optional[Dict]:
        """Legacy method for backward compatibility"""
        return await self.search(
            query=query,
            num_results=num_results,
            use_autoprompt=use_autoprompt,
            type=type,
            summary=True
        )

    async def close(self):
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()


class WebSearchEngine:
    """Engine for intelligent web search integration"""

    def __init__(self):
        self.exa_client = ExaSearchClient()
        self.search_cache = {}  # Simple cache for recent searches
        self.cache_ttl = 3600  # 1 hour cache TTL

    async def close(self):
        """Close the web search engine"""
        await self.exa_client.close()


# Global web search engine instance
web_search_engine = WebSearchEngine()
