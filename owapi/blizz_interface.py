"""
Interface that uses Blizzard's pages as the source.
"""
import asyncio
import functools
import logging
import traceback

import aiohttp
from kyoukai.asphalt import HTTPRequestContext
from lxml import etree

try:
    from html5_parser import parse
    _has_html5_parser = True
except ImportError:
    _has_html5_parser = False
from werkzeug.exceptions import HTTPException, NotFound, InternalServerError

from owapi import util

B_BASE_URL = "https://playoverwatch.com/en-us/"
B_PAGE_URL = B_BASE_URL + "career/{platform}/{btag}"
B_HEROES_URL = B_BASE_URL + "heroes"
B_HERO_URL = B_HEROES_URL + "/{hero}"

logger = logging.getLogger("OWAPI")


async def get_page_body(ctx: HTTPRequestContext, url: str, cache_time=300, cache_404=False) -> str:
    """
    Downloads page body from PlayOverwatch and caches it.
    """

    async def _real_get_body(_, url: str):
        # Real function.
        logger.info("GET => {}".format(url))
        async with ctx.session.get(url) as req:
            assert isinstance(req, aiohttp.ClientResponse)
            logger.info("GET => {} => {}".format(url, req.status))
            if req.status != 200:
                return None
            return (await req.read()).decode()

    result = await util.with_cache(ctx, _real_get_body, url, expires=cache_time,
                                   cache_404=cache_404)
    return result


def _parse_page_html5(content: str) -> etree._Element:
    """
    Internal function to parse a page and return the data.

    This uses html5_parser.
    """
    if content and content.lower() != 'none':
        data = parse(content)
        return data


def _parse_page_lxml(content: str) -> etree._Element:
    """
    Internal function to parse a page and return the data.

    This uses raw LXML.
    """
    if content and content.lower() != 'none':
        data = etree.HTML(content)
        return data


async def get_user_page(ctx: HTTPRequestContext, battletag: str, platform: str = "pc",
                        cache_time=300, cache_404=False) -> etree._Element:
    """
    Downloads the BZ page for a user, and parses it.
    """
    built_url = B_PAGE_URL.format(btag=battletag.replace("#", "-"), platform=platform)
    page_body = await get_page_body(ctx, built_url, cache_time=cache_time, cache_404=cache_404)

    if not page_body:
        return None

    # parse the page
    parse_partial = functools.partial(_parse_page, page_body)
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, parse_partial)

    # sanity check
    node = parsed.findall(".//section[@class='u-nav-offset']//h1[@class='u-align-center']")
    for nodes in node:
        if nodes.text.strip() == "Profile Not Found":
            return None

    return parsed


async def fetch_all_user_pages(ctx: HTTPRequestContext, battletag: str, *,
                               platform="pc"):
    """
    Fetches all user pages for a specified user.

    Returns a dictionary in the format of `{"any": etree._Element | None}`.
    """
    if platform != "pc":
        coro = get_user_page(ctx, battletag, platform=platform, cache_404=True)
        result = await coro
        if isinstance(result, etree._Element):
            return {"any": result}
        else:
            # Raise a 404.
            raise NotFound()

    futures = []
    coro = get_user_page(ctx, battletag, platform=platform, cache_404=True)
    futures.append(coro)

    # Gather all the futures to download in parallel.
    results = await asyncio.gather(*futures, return_exceptions=True)
    user_data = {"any": None}
    error = None
    for key, result in zip(["any"], results):
        # Make sure it's either a None or an element.
        if isinstance(result, etree._Element):
            user_data[key] = result
        elif isinstance(result, Exception):
            logger.error("Failed to fetch user page!\n{}".format(
                ''.join(traceback.format_exception(type(result), result, result.__traceback__))
            ))
            error = result
            user_data[key] = None
        else:
            user_data[key] = None

    # Check if we should raise or return.
    if user_data["any"] is None:
        if error is not None:
            e = InternalServerError()
            e.__cause__ = error
            e.__context__ = error
            raise e
        raise NotFound()

    return user_data


async def get_hero_data(ctx: HTTPRequestContext, hero: str):
    built_url = B_HERO_URL.format(hero=hero)
    page_body = await get_page_body(ctx, built_url)

    if not page_body:
        raise HTTPException(404)

    parse_partial = functools.partial(_parse_page, page_body)
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, parse_partial)

    return parsed


async def get_all_heroes(ctx: HTTPRequestContext):
    built_url = B_HEROES_URL
    page_body = await get_page_body(ctx, built_url)

    if not page_body:
        raise HTTPException(404)

    parse_partial = functools.partial(_parse_page, page_body)
    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, parse_partial)

    return parsed


if _has_html5_parser:
    _parse_page = _parse_page_html5
else:
    _parse_page = _parse_page_lxml
