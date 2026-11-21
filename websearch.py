#!/usr/bin/env python3
"""NYXOR v2 - web search through whichever search API key you added (Tavily, Brave, Serper, Exa, SerpAPI, Jina,
Firecrawl), with a no-key DuckDuckGo fallback. All adapters return [{title, url, snippet, date}]. Results go into the
prompt, so every model can use them (no tool-calling support needed). Fresh-news questions use the news endpoints."""
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

import db
import privacy
import providers as P
from providers import ProviderError

BING = {"id": "bing", "name": "Bing News (no key)", "protocol": "bing", "base_url": "https://www.bing.com", "api_key": "", "kind": "search", "enabled": 1}
DDG = {"id": "ddg", "name": "DuckDuckGo (no key)", "protocol": "ddg", "base_url": "https://html.duckduckgo.com", "api_key": "", "kind": "search", "enabled": 1}


def _call(p, method, path, params=None, body=None, headers=None, raw=False, base=None):
    url = (base or p["base_url"]).rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = {"User-Agent": "Mozilla/5.0 (compatible; NYXOR/2)", "Accept": "application/json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8", "replace")
            return txt if raw else json.loads(txt or "{}")
    except urllib.error.HTTPError as e:
        hint = " The key was rejected." if e.code in (401, 403) else " Rate limit or quota reached." if e.code == 429 else ""
        raise ProviderError("%s search failed (HTTP %d).%s" % (p["name"], e.code, hint), e.code)
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as e:
        raise ProviderError("Cannot reach %s: %s" % (p["name"], getattr(e, "reason", e)))


def _norm(items, t="title", u="url", s="snippet", d=None):
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get(u):
            out.append({"title": str(it.get(t) or it[u])[:200], "url": str(it[u]), "snippet": " ".join(str(it.get(s) or "").split())[:500],
                        "date": str(it.get(d) or "")[:40] if d else ""})
    return out


def _tavily(p, q, n, news=False):
    body = {"query": q, "max_results": n, "search_depth": "basic"}
    if news:
        body.update(topic="news", days=7)
    j = _call(p, "POST", "/search", body=body, headers={"Authorization": "Bearer " + p["api_key"]})
    return _norm(j.get("results"), s="content", d="published_date")


def _brave(p, q, n, news=False):
    j = _call(p, "GET", "/res/v1/news/search" if news else "/res/v1/web/search", {"q": q, "count": n}, headers={"X-Subscription-Token": p["api_key"]})
    return _norm(j.get("results") if news else (j.get("web") or {}).get("results"), s="description", d="age")


def _serper(p, q, n, news=False):
    j = _call(p, "POST", "/news" if news else "/search", body={"q": q, "num": n}, headers={"X-API-KEY": p["api_key"]})
    return _norm(j.get("news") if news else j.get("organic"), u="link", d="date")


def _exa(p, q, n, news=False):
    body = {"query": q, "numResults": n, "contents": {"text": {"maxCharacters": 500}}}
    if news:
        body["category"] = "news"
    j = _call(p, "POST", "/search", body=body, headers={"x-api-key": p["api_key"]})
    return _norm(j.get("results"), s="text", d="publishedDate")


def _serpapi(p, q, n, news=False):
    prm = {"engine": "google", "q": q, "num": n, "api_key": p["api_key"]}
    if news:
        prm["tbm"] = "nws"
    j = _call(p, "GET", "/search.json", prm)
    return _norm(j.get("news_results") if news else j.get("organic_results"), u="link", d="date")


def _jina(p, q, n, news=False):
    j = _call(p, "GET", "/", {"q": q}, headers={"Authorization": "Bearer " + p["api_key"], "X-Respond-With": "no-content"})
    return _norm(j.get("data"), s="description")


def _firecrawl(p, q, n, news=False):
    j = _call(p, "POST", "/v1/search", body={"query": q, "limit": n}, headers={"Authorization": "Bearer " + p["api_key"]})
    return _norm(j.get("data"), s="description")


def _bing(p, q, n, news=False):
    """Keyless fresh news: Bing News RSS. Items link through bing.com/news/apiclick with the real URL in the 'url' parameter."""
    import xml.etree.ElementTree as ET
    xml = _call(p, "GET", "/news/search", {"q": q, "format": "RSS", "count": n}, headers={"Accept": "application/rss+xml,text/xml"}, raw=True)
    try:
        root = ET.fromstring(xml[:1_500_000])
    except ET.ParseError:
        raise ProviderError("Bing News returned something that is not RSS.")
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        real = qs.get("url", [link])[0]
        if real.startswith("http"):
            out.append({"title": " ".join((it.findtext("title") or real).split())[:200], "url": real,
                        "snippet": " ".join(unescape(re.sub(r"<[^>]+>", "", it.findtext("description") or "")).split())[:500], "date": (it.findtext("pubDate") or "")[:40]})
        if len(out) >= n:
            break
    if not out:
        raise ProviderError("Bing News returned no results.")
    return out


def _ddg(p, q, n, news=False):
    """Keyless fallback: parses DuckDuckGo's HTML results page. Best effort: it can be rate-limited or change layout."""
    html = _call(p, "GET", "/html/", {"q": q}, headers={"Accept": "text/html"}, raw=True)
    out, snips = [], re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    for i, m in enumerate(re.finditer(r'<a\b([^>]*class="[^"]*result__a[^"]*"[^>]*)>(.*?)</a>', html, re.S)):
        href = re.search(r'href="([^"]+)"', m.group(1))
        if not href:
            continue
        url = unescape(href.group(1))
        if url.startswith("//"):
            url = "https:" + url
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "uddg" in qs:
            url = qs["uddg"][0]
        if not url.startswith("http"):
            continue
        out.append({"title": unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()[:200] or url, "url": url,
                    "snippet": unescape(re.sub(r"<[^>]+>", "", snips[i])).strip()[:500] if i < len(snips) else "", "date": ""})
        if len(out) >= n:
            break
    if not out:     # second chance: the lite page
        try:
            lite = _call(p, "GET", "/lite/", {"q": q}, headers={"Accept": "text/html"}, raw=True, base=p.get("lite_url") or "https://lite.duckduckgo.com")
        except ProviderError:
            lite = ""
        sn = re.findall(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", lite, re.S)
        for i, m in enumerate(re.finditer(r"<a\b([^>]*result-link[^>]*)>(.*?)</a>", lite, re.S)):
            href = re.search(r"href=['\"]([^'\"]+)['\"]", m.group(1))
            if not href:
                continue
            url = unescape(href.group(1))
            url = ("https:" + url) if url.startswith("//") else url
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            url = qs["uddg"][0] if "uddg" in qs else url
            if url.startswith("http"):
                out.append({"title": unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()[:200] or url, "url": url,
                            "snippet": unescape(re.sub(r"<[^>]+>", "", sn[i])).strip()[:500] if i < len(sn) else "", "date": ""})
            if len(out) >= n:
                break
    if not out:
        raise ProviderError("DuckDuckGo returned no results (it may have blocked the request). Add a Tavily, Brave or Serper key for reliable search.")
    return out


ADAPTERS = {"tavily": _tavily, "brave": _brave, "serper": _serper, "exa": _exa, "serpapi": _serpapi, "jina": _jina,
            "firecrawl": _firecrawl, "ddg": _ddg, "bing": _bing}


def search(p, query, n=5, news=False):
    fn = ADAPTERS.get(p.get("protocol"))
    if not fn:
        raise ProviderError("%s is not a search provider." % p["name"])
    P.consent("web_research")
    if p["protocol"] not in ("ddg", "bing") and not p.get("api_key"):
        raise ProviderError("%s has no API key." % p["name"])
    return fn(p, query.strip()[:300], n, news)[:n]


def pick(allow_keyless=None, news=False):
    """The search provider to use: the preferred keyed one, else the first keyed one, else (if allowed) a keyless one
    (Bing News RSS for news questions, DuckDuckGo otherwise)."""
    want = db.get_setting("search_provider") or ""
    rows = [db.get_provider(r["id"]) for r in db.list_providers() if r["kind"] == "search"]
    usable = [p for p in rows if p["enabled"] and p["api_key"]]
    hit = next((p for p in usable if p["id"] == want), usable[0] if usable else None)
    if hit:
        return hit
    if allow_keyless is None:
        allow_keyless = bool(db.get_setting("web_keyless")) and not privacy.store()    # the Store build never scrapes search pages
    return dict(BING if news else DDG) if allow_keyless else None


def keyless_fallbacks(p, news):
    """Other keyless sources to try when a keyless search fails (never used for keyed providers)."""
    if p.get("protocol") not in ("ddg", "bing"):
        return []
    return [dict(DDG), dict(BING)] if p["protocol"] == "bing" else ([dict(BING)] if news else [])


def test(p):
    import time
    t0 = time.time()
    try:
        res = search(p, "NYXOR connectivity test", 1)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "message": "%d result(s)" % len(res)}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}
