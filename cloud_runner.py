from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "config" / "sources.yaml"
INTERESTS_FILE = ROOT / "config" / "china_interest_map.yaml"
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
SITE_DATA = SITE_DIR / "data"
ARTICLES_FILE = DATA_DIR / "articles.json"
STATUS_FILE = DATA_DIR / "run_status.json"

USER_AGENT = os.getenv("USER_AGENT", "GlobalChinaNewsResearchBot/0.2 (+research; GitHub Actions)")
FETCH_TIMEOUT = int(os.getenv("FETCH_TIMEOUT_SECONDS", "18"))
MAX_PER_SOURCE = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "80"))
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "36"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "18"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "45"))
MAX_STORED = int(os.getenv("MAX_STORED_ARTICLES", "50000"))
SITE_MAX = int(os.getenv("SITE_MAX_ARTICLES", "20000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
CLASSIFIER_BATCH = int(os.getenv("CLASSIFIER_BATCH_SIZE", "25"))
ENABLE_GDELT = os.getenv("ENABLE_GDELT", "true").lower() in {"1", "true", "yes", "on"}
GDELT_TIMESPAN = os.getenv("GDELT_TIMESPAN", "1d")

TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid"}
ARTICLE_HINTS = re.compile(r"/(20\d{2}/|article|articles|news|story|stories|analysis|research|report|reports|publication|publications|commentary|insight|insights|press|blog|opinion|policy|event|events)/", re.I)
SKIP_HINTS = re.compile(r"/(tag|topic|author|authors|category|categories|about|contact|privacy|terms|login|subscribe|newsletter|podcast|video|videos)(/|$)", re.I)
DIRECT_TERMS = re.compile(
    r"\b(china|chinese|beijing|prc|people'?s republic of china|xi jinping|ccp|cpc|pla|renminbi|yuan|hong kong|xinjiang|tibet|taiwan)\b|"
    r"\b(huawei|byd|catl|cosco|zte|smic|bytedance|tiktok|cnooc|sinopec|petrochina|crrc|alibaba|tencent|lenovo|geely|saic|chery|great wall motor|china railway|state grid)\b",
    re.I,
)
MAJOR_EVENT = re.compile(
    r"\b(election|electoral|vote|government|cabinet|president|prime minister|coalition|parliament|congress|policy|regulation|law|bill|ban|tariff|sanction|blacklist|export control|investment|subsidy|review|probe|investigation|military|defen[cs]e|war|conflict|coup|protest|strike|riot|unrest|terror|port|mine|mining|copper|lithium|nickel|rare earth|oil|gas|energy|rail|railway|telecom|semiconductor|chip|battery|ev|electric vehicle|infrastructure|nuclear|space|satellite|cyber|data|artificial intelligence|\bai\b|trade|supply chain|shipping|shipping lane|currency|central bank|interest rate|foreign policy|diplomatic|diplomacy|alliance|nato|eu|european union)\b",
    re.I,
)
SPORT_ENTERTAINMENT = re.compile(r"\b(football|soccer|basketball|baseball|tennis|golf|formula 1|f1|olympic|league|cup final|celebrity|actor|actress|movie|film festival|music|singer|concert|fashion|recipe)\b", re.I)

GDELT_QUERIES = [
    '(China OR Chinese OR Beijing OR PRC OR "People\'s Republic of China")',
    '(Huawei OR BYD OR CATL OR COSCO OR ByteDance OR TikTok OR SMIC OR "China Railway" OR CNOOC OR Sinopec)',
    '(Taiwan OR "South China Sea" OR "Belt and Road" OR "Chinese investment" OR "Chinese company")',
]

@dataclass
class RawItem:
    title: str
    url: str
    source_name: str
    source_kind: str = "news"
    source_country: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    snippet: str | None = None

@dataclass
class Decision:
    relation: str
    reason: str
    entities: list[str]
    confidence: int
    classifier: str


def log(msg: str):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC] {msg}", flush=True)


def compact_text(text: str | None, limit: int = 1200) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()[:limit]


def canonicalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING]
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))
    except Exception:
        return url.strip()


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sources() -> list[dict]:
    return load_yaml(SOURCES_FILE).get("sources", [])


def load_interests() -> dict:
    return load_yaml(INTERESTS_FILE)


def profile_aliases(profile_key: str, profile: dict) -> list[str]:
    return [profile_key] + [str(x) for x in (profile.get("aliases") or [])]


def text_has_alias(text: str, alias: str) -> bool:
    a = alias.strip().lower()
    if len(a) < 3:
        return False
    if re.search(r"[a-z]", a):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", text))
    return a in text


def match_interest_profiles(title: str, snippet: str | None, source_country: str | None, interests: dict) -> tuple[list[str], list[str], str]:
    profiles = interests.get("profiles", {})
    text = f"{title} {snippet or ''}".lower()
    matched_profiles: list[str] = []
    matched_entities: list[str] = []

    if source_country:
        c = source_country.strip().lower()
        for key, p in profiles.items():
            if any(c == a.lower() for a in profile_aliases(key, p)):
                matched_profiles.append(key)
                break

    for key, p in profiles.items():
        if key not in matched_profiles and any(text_has_alias(text, a) for a in profile_aliases(key, p)):
            matched_profiles.append(key)
        for ent in (p.get("entities") or []):
            ent_s = str(ent).strip()
            if len(ent_s) >= 4 and text_has_alias(text, ent_s) and ent_s not in matched_entities:
                matched_entities.append(ent_s)
                if key not in matched_profiles:
                    matched_profiles.append(key)

    contexts = []
    for key in matched_profiles[:4]:
        p = profiles.get(key, {})
        parts = [f"事件关联地区={key}"]
        if p.get("interests"):
            parts.append("中国关联利益=" + ", ".join(map(str, p["interests"][:10])))
        if p.get("entities"):
            parts.append("中国关联实体/项目=" + ", ".join(map(str, p["entities"][:14])))
        contexts.append("; ".join(parts))
    return matched_profiles[:8], matched_entities[:15], " | ".join(contexts)


def heuristic_decision(item: RawItem, interests: dict) -> Decision:
    text = f"{item.title} {item.snippet or ''}"
    if DIRECT_TERMS.search(text):
        return Decision("direct", "标题或摘要直接出现中国、中国相关地区/机构/企业/实体", [], 96, "rules")
    profiles, entities, ctx = match_interest_profiles(item.title, item.snippet, item.source_country, interests)
    if entities:
        return Decision("indirect", f"标题或摘要命中中国海外关联企业/项目；{ctx}", entities, 86, "rules")
    generic = not profiles or all(x.lower() in {"global", "europe", "africa", "nato", "european union"} for x in profiles)
    if profiles and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text) and not generic:
        return Decision("potential", f"发生在存在中国重要利益关联的国家/地区，且涉及重大政治、经济、产业或安全事件；{ctx}", [], 68, "rules")
    return Decision("unrelated", "未发现足够的直接或广义涉华关联", [], 55, "rules")


def is_candidate_for_ai(item: RawItem, interests: dict) -> tuple[bool, dict]:
    profiles, entities, ctx = match_interest_profiles(item.title, item.snippet, item.source_country, interests)
    text = f"{item.title} {item.snippet or ''}"
    if entities:
        return True, {"profiles": profiles, "entities": entities, "context": ctx}
    if profiles and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text):
        return True, {"profiles": profiles, "entities": [], "context": ctx}
    return False, {"profiles": profiles, "entities": entities, "context": ctx}


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    try:
        p = urlparse(url)
        robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
        r = await client.get(robots_url, timeout=min(FETCH_TIMEOUT, 8))
        if r.status_code >= 400:
            return True
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(r.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


async def discover_feed(client: httpx.AsyncClient, homepage: str) -> str | None:
    try:
        r = await client.get(homepage)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for link in soup.find_all("link", href=True):
            typ = (link.get("type") or "").lower()
            rel = " ".join(link.get("rel") or []).lower()
            if "alternate" in rel and ("rss" in typ or "atom" in typ or "xml" in typ):
                return urljoin(str(r.url), link["href"])
        for path in ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml"):
            candidate = urljoin(str(r.url), path)
            try:
                rr = await client.get(candidate, timeout=min(FETCH_TIMEOUT, 10))
                head = rr.text[:500].lower()
                ctype = rr.headers.get("content-type", "").lower()
                if rr.status_code < 400 and ("xml" in ctype or "<rss" in head or "<feed" in head):
                    return candidate
            except Exception:
                pass
    except Exception:
        pass
    return None


async def fetch_feed(client: httpx.AsyncClient, source: dict, feed_url: str) -> list[RawItem]:
    if not await robots_allowed(client, feed_url):
        return []
    r = await client.get(feed_url)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out: list[RawItem] = []
    for e in feed.entries[:MAX_PER_SOURCE]:
        title = compact_text(html.unescape(getattr(e, "title", "")), 500)
        url = getattr(e, "link", "")
        if not title or not url:
            continue
        summary = getattr(e, "summary", None) or getattr(e, "description", None)
        published = getattr(e, "published", None) or getattr(e, "updated", None)
        out.append(RawItem(
            title=title,
            url=url,
            source_name=source["name"],
            source_kind=source.get("kind", "news"),
            source_country=source.get("country"),
            language=source.get("language"),
            published_at=parse_date(published),
            snippet=compact_text(BeautifulSoup(summary or "", "lxml").get_text(" "), 1000),
        ))
    return out


async def fetch_homepage(client: httpx.AsyncClient, source: dict, url: str) -> list[RawItem]:
    if not await robots_allowed(client, url):
        return []
    r = await client.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    host = urlparse(str(r.url)).netloc
    seen = set()
    candidates: list[RawItem] = []
    for a in soup.find_all("a", href=True):
        title = compact_text(a.get_text(" ", strip=True), 500)
        if len(title) < 18:
            continue
        href = urljoin(str(r.url), a["href"])
        p = urlparse(href)
        if p.scheme not in ("http", "https") or p.netloc != host or SKIP_HINTS.search(p.path):
            continue
        if not ARTICLE_HINTS.search(p.path) and len(title) < 42:
            continue
        canon = canonicalize_url(href)
        if canon in seen:
            continue
        seen.add(canon)
        candidates.append(RawItem(
            title=title,
            url=href,
            source_name=source["name"],
            source_kind=source.get("kind", "news"),
            source_country=source.get("country"),
            language=source.get("language"),
        ))
        if len(candidates) >= MAX_PER_SOURCE:
            break
    return candidates


async def fetch_source(client: httpx.AsyncClient, source: dict) -> list[RawItem]:
    mode = source.get("mode", "auto")
    if mode == "disabled":
        return []
    feed_url = source.get("feed")
    if feed_url:
        try:
            return await fetch_feed(client, source, feed_url)
        except Exception:
            pass
    if mode in {"auto", "feed"}:
        feed_url = await discover_feed(client, source["homepage"])
        if feed_url:
            try:
                return await fetch_feed(client, source, feed_url)
            except Exception:
                pass
    return await fetch_homepage(client, source, source.get("listing") or source["homepage"])


async def fetch_gdelt(client: httpx.AsyncClient) -> list[RawItem]:
    if not ENABLE_GDELT:
        return []
    out: list[RawItem] = []
    seen = set()
    for query in GDELT_QUERIES:
        params = {"query": query, "mode": "ArtList", "maxrecords": 250, "format": "json", "timespan": GDELT_TIMESPAN, "sort": "DateDesc"}
        try:
            r = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=30)
            r.raise_for_status()
            for a in r.json().get("articles", []):
                title, url = compact_text(a.get("title"), 500), a.get("url")
                if not title or not url:
                    continue
                canon = canonicalize_url(url)
                if canon in seen:
                    continue
                seen.add(canon)
                out.append(RawItem(title=title, url=url, source_name=a.get("domain") or "GDELT source", source_kind="news", source_country=a.get("sourcecountry"), language=a.get("language"), published_at=parse_date(a.get("seendate"))))
        except Exception as e:
            log(f"GDELT query failed: {type(e).__name__}")
    return out


async def collect_all(sources: list[dict]) -> tuple[list[RawItem], list[dict]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"}
    limits = httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=max(10, MAX_CONNECTIONS // 2))
    timeout = httpx.Timeout(FETCH_TIMEOUT)
    sem = asyncio.Semaphore(CONCURRENCY)
    errors: list[dict] = []
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout, limits=limits) as client:
        async def one(src):
            async with sem:
                try:
                    rows = await fetch_source(client, src)
                    return rows
                except Exception as e:
                    errors.append({"source": src.get("name", "?"), "error": f"{type(e).__name__}: {compact_text(str(e), 180)}"})
                    return []
        batches = await asyncio.gather(*(one(s) for s in sources))
        items = [x for batch in batches for x in batch]
        items.extend(await fetch_gdelt(client))
    return items, errors


def extract_json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    return json.loads(text)


def classify_ai_batch(items: list[RawItem], metadata: list[dict], interests: dict) -> list[Decision]:
    if not OPENAI_API_KEY:
        return [heuristic_decision(x, interests) for x in items]
    payload = []
    for i, (item, meta) in enumerate(zip(items, metadata)):
        payload.append({
            "id": i,
            "title": compact_text(item.title, 500),
            "snippet": compact_text(item.snippet, 700),
            "source": item.source_name,
            "source_country": item.source_country,
            "source_kind": item.source_kind,
            "known_china_interest_context": meta.get("context", ""),
            "matched_interest_profiles": meta.get("profiles", []),
            "matched_entities": meta.get("entities", []),
        })
    prompt = """你是全球新闻标题库的入池分类器。只判断新闻是否具有“广义涉华研究关联”，不要判断对中国有利或不利，不做政策研判。\n\n分类：\n- direct：新闻直接涉及中国、中国政府/军队/地区、中国企业/人员/资本/项目。\n- indirect：标题可能不写中国，但事件明确涉及中国在当地的企业、投资、项目、供应链、资源、港口、通信、科技、人员或外交合作。\n- potential：文本没有直接提中国，但这是与中国存在重要利益暴露的国家/地区/行业发生的重大政权更迭、选举、外交路线调整、监管/投资/贸易/产业政策变化、战争/政变/抗议/罢工、关键资源/能源/港口/基础设施变化等，值得中国研究人员查看。例如：匈牙利大选导致政权更迭并宣称回归欧洲，应至少为 potential，因为可能涉及中国在匈重大投资与双边合作。\n- unrelated：没有足够涉华研究关联。\n\n原则：宁可适度多收录，不要漏掉间接/潜在涉华的重大事件；普通体育、娱乐、天气、生活资讯和一般犯罪不要因为国家与中国有关系就收录。\n只输出JSON数组，每项严格为：id, relation(direct/indirect/potential/unrelated), reason(一句中文，只解释为什么与中国有关), entities(字符串数组), confidence(0-100整数)。\n\n待判断：\n""" + json.dumps(payload, ensure_ascii=False)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.responses.create(model=OPENAI_MODEL, input=prompt)
        rows = extract_json_array(resp.output_text)
        by_id = {int(r.get("id")): r for r in rows if "id" in r}
        out = []
        for i, item in enumerate(items):
            r = by_id.get(i)
            if not r:
                out.append(heuristic_decision(item, interests)); continue
            relation = r.get("relation", "unrelated")
            if relation not in {"direct", "indirect", "potential", "unrelated"}:
                relation = "unrelated"
            try: conf = max(0, min(100, int(r.get("confidence", 70))))
            except Exception: conf = 70
            out.append(Decision(relation, compact_text(r.get("reason") or "", 800), [compact_text(str(x), 120) for x in (r.get("entities") or [])[:12]], conf, OPENAI_MODEL))
        return out
    except Exception as e:
        log(f"AI classification failed, fallback to rules: {type(e).__name__}: {compact_text(str(e), 200)}")
        return [heuristic_decision(x, interests) for x in items]


def classify_items(items: list[RawItem], interests: dict) -> list[tuple[RawItem, Decision]]:
    results: list[tuple[RawItem, Decision]] = []
    candidates: list[RawItem] = []
    candidate_meta: list[dict] = []
    for item in items:
        text = f"{item.title} {item.snippet or ''}"
        if DIRECT_TERMS.search(text):
            results.append((item, Decision("direct", "标题或摘要直接出现中国、中国相关地区/机构/企业/实体", [], 96, "rules")))
            continue
        is_candidate, meta = is_candidate_for_ai(item, interests)
        if not is_candidate:
            continue
        if not OPENAI_API_KEY:
            d = heuristic_decision(item, interests)
            if d.relation != "unrelated":
                results.append((item, d))
        else:
            candidates.append(item); candidate_meta.append(meta)

    if OPENAI_API_KEY:
        for start in range(0, len(candidates), CLASSIFIER_BATCH):
            batch = candidates[start:start + CLASSIFIER_BATCH]
            meta = candidate_meta[start:start + CLASSIFIER_BATCH]
            decisions = classify_ai_batch(batch, meta, interests)
            for item, d in zip(batch, decisions):
                if d.relation != "unrelated":
                    results.append((item, d))
    return results


def load_existing() -> list[dict]:
    try:
        data = json.loads(ARTICLES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def article_record(item: RawItem, decision: Decision, collected_at: datetime) -> dict:
    canon = canonicalize_url(item.url)
    key = hashlib.sha256(canon.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return {
        "id": key,
        "title": item.title,
        "source": item.source_name,
        "source_kind": item.source_kind,
        "country": item.source_country or "",
        "language": item.language or "",
        "published_at": iso(item.published_at),
        "collected_at": iso(collected_at),
        "url": item.url,
        "canonical_url": canon,
        "relation": decision.relation,
        "reason": decision.reason,
        "entities": decision.entities,
        "confidence": decision.confidence,
        "classifier": decision.classifier,
    }


def sort_time(row: dict) -> str:
    return row.get("published_at") or row.get("collected_at") or ""


def merge_and_store(existing: list[dict], new_records: list[dict]) -> list[dict]:
    by_url = {r.get("canonical_url") or canonicalize_url(r.get("url", "")): r for r in existing if r.get("url")}
    for r in new_records:
        by_url[r["canonical_url"]] = r
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept = []
    for r in by_url.values():
        dt = parse_date(r.get("published_at") or r.get("collected_at"))
        if dt is None or dt >= cutoff:
            kept.append(r)
    kept.sort(key=sort_time, reverse=True)
    return kept[:MAX_STORED]


def build_site(articles: list[dict], status: dict, sources: list[dict]):
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    site_rows = articles[:SITE_MAX]
    (SITE_DATA / "articles.json").write_text(json.dumps(site_rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE_DATA / "run_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    kinds = {}
    for s in sources:
        kinds[s.get("kind", "news")] = kinds.get(s.get("kind", "news"), 0) + 1
    (SITE_DATA / "source_summary.json").write_text(json.dumps({"total": len(sources), "kinds": kinds}, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = SITE_DATA / "latest.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "国家/地区", "来源", "来源类型", "原标题", "广义涉华类型", "命中原因", "关联实体", "置信度", "原文链接"])
        for r in site_rows:
            w.writerow([r.get("published_at") or r.get("collected_at"), r.get("country", ""), r.get("source", ""), r.get("source_kind", ""), r.get("title", ""), r.get("relation", ""), r.get("reason", ""), ", ".join(r.get("entities") or []), r.get("confidence", ""), r.get("url", "")])
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (SITE_DIR / "index.html").write_text(SITE_HTML, encoding="utf-8")


SITE_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全球广义涉华新闻标题库</title>
<style>
:root{--bg:#f5f7fa;--ink:#152033;--muted:#687386;--line:#e2e7ee;--nav:#152033;--card:#fff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}header{background:var(--nav);color:#fff;padding:22px 24px}.wrap{max-width:1600px;margin:auto}h1{margin:0;font-size:25px}.sub{margin-top:7px;color:#c9d3df;font-size:13px;line-height:1.5}.notice{margin:14px 0;padding:10px 13px;background:#fff8e6;border:1px solid #f0d799;border-radius:9px;font-size:13px}.stats{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:11px 15px;min-width:140px}.card b{display:block;font-size:23px}.card span{font-size:12px;color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}select,input,button,a.btn{border:1px solid #ccd4df;border-radius:7px;background:#fff;padding:8px 10px;font-size:13px;color:var(--ink);text-decoration:none}button{background:#152033;color:#fff;cursor:pointer}.tablebox{margin-top:12px;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:auto;max-height:calc(100vh - 310px)}table{border-collapse:collapse;width:100%;min-width:1250px}th,td{padding:9px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top;font-size:13px}th{position:sticky;top:0;background:#f8fafc;z-index:2}.title{font-weight:650;font-size:14px;line-height:1.45;max-width:590px}.reason{color:#4d596a;line-height:1.45;max-width:390px}.muted{font-size:11px;color:var(--muted)}.tag{display:inline-block;border-radius:999px;padding:3px 7px;font-size:11px}.direct{background:#fde2e2}.indirect{background:#ffead8}.potential{background:#fff1bd}.pager{display:flex;gap:8px;align-items:center;padding:13px 0 30px}.right{margin-left:auto}.loading{padding:30px;text-align:center;color:var(--muted)}@media(max-width:700px){header{padding:18px 14px}.wrap{padding-left:8px;padding-right:8px}.tablebox{max-height:none}.reasoncol{display:none}}
</style></head><body>
<header><div class="wrap"><h1>全球广义涉华新闻标题库</h1><div class="sub">自动抓取国外新闻、智库、调查机构和官方公开发布源。系统只判断“是否具有广义涉华研究关联”，不判断对华有利或不利。</div></div></header>
<main class="wrap" style="padding:4px 18px 30px"><div class="notice" id="status">正在读取云端抓取结果……</div><div class="stats"><div class="card"><b id="total">-</b><span>当前筛选结果</span></div><div class="card"><b id="direct">-</b><span>直接涉华</span></div><div class="card"><b id="indirect">-</b><span>间接涉华</span></div><div class="card"><b id="potential">-</b><span>潜在涉华</span></div><div class="card"><b id="sources">-</b><span>每日巡检来源</span></div></div>
<div class="filters"><select id="hours"><option value="24">最近24小时</option><option value="72">最近3天</option><option value="168">最近7天</option><option value="720">最近30天</option><option value="99999">全部保留数据</option></select><select id="relation"><option value="">全部涉华类型</option><option value="direct">直接涉华</option><option value="indirect">间接涉华</option><option value="potential">潜在涉华</option></select><select id="country"><option value="">全部国家/地区</option></select><select id="kind"><option value="">全部来源类型</option><option value="news">新闻媒体</option><option value="think_tank">智库/研究机构</option><option value="official">政府/国际机构</option></select><select id="source"><option value="">全部来源</option></select><input id="q" type="search" size="30" placeholder="搜标题 / 命中原因 / 实体"><button id="go">筛选</button><a class="btn" href="data/latest.csv">下载CSV</a><span class="right muted" id="shown"></span></div>
<div class="tablebox"><table><thead><tr><th>时间</th><th>国家/地区</th><th>来源</th><th>原标题</th><th>涉华类型</th><th class="reasoncol">命中原因（只解释关联）</th><th>原文</th></tr></thead><tbody id="tbody"><tr><td colspan="7" class="loading">加载中……</td></tr></tbody></table></div><div class="pager"><button id="prev">上一页</button><span id="page"></span><button id="next">下一页</button></div></main>
<script>
let all=[], filtered=[], page=1; const size=100; const label={direct:'直接',indirect:'间接',potential:'潜在'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function when(r){return r.published_at||r.collected_at||''} function dt(s){const d=new Date(s);return isNaN(d)?'':d.toLocaleString('zh-CN',{hour12:false})}
function fillSelect(id,vals){const el=document.getElementById(id); const first=el.options[0].outerHTML; el.innerHTML=first+[...new Set(vals.filter(Boolean))].sort((a,b)=>a.localeCompare(b)).map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')}
function apply(){const hrs=+document.getElementById('hours').value, rel=document.getElementById('relation').value,c=document.getElementById('country').value,k=document.getElementById('kind').value,s=document.getElementById('source').value,q=document.getElementById('q').value.trim().toLowerCase(); const cutoff=Date.now()-hrs*3600e3; filtered=all.filter(r=>(hrs>90000||new Date(when(r)).getTime()>=cutoff)&&(!rel||r.relation===rel)&&(!c||r.country===c)&&(!k||r.source_kind===k)&&(!s||r.source===s)&&(!q||`${r.title} ${r.reason} ${(r.entities||[]).join(' ')}`.toLowerCase().includes(q))); page=1; render()}
function render(){const start=(page-1)*size, rows=filtered.slice(start,start+size); let counts={direct:0,indirect:0,potential:0}; filtered.forEach(r=>counts[r.relation]=(counts[r.relation]||0)+1); document.getElementById('total').textContent=filtered.length.toLocaleString();['direct','indirect','potential'].forEach(x=>document.getElementById(x).textContent=(counts[x]||0).toLocaleString()); document.getElementById('shown').textContent=`显示 ${rows.length} 条 / 共 ${filtered.length} 条`;
 document.getElementById('tbody').innerHTML=rows.length?rows.map(r=>`<tr><td class="muted">${esc(dt(when(r)))}</td><td>${esc(r.country)}</td><td>${esc(r.source)}<div class="muted">${esc(r.source_kind)}${r.language?' · '+esc(r.language):''}</div></td><td class="title">${esc(r.title)}</td><td><span class="tag ${esc(r.relation)}">${label[r.relation]||esc(r.relation)}</span>${r.confidence?`<div class="muted">${esc(r.confidence)}%</div>`:''}</td><td class="reason reasoncol">${esc(r.reason)}${(r.entities||[]).length?`<div class="muted">关联：${esc(r.entities.join(', '))}</div>`:''}</td><td><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">查看原文</a></td></tr>`).join(''):'<tr><td colspan="7" class="loading">当前筛选条件没有结果</td></tr>'; const pages=Math.max(1,Math.ceil(filtered.length/size));document.getElementById('page').textContent=`第 ${page} / ${pages} 页`;document.getElementById('prev').disabled=page<=1;document.getElementById('next').disabled=page>=pages}
async function init(){try{const [a,st,ss]=await Promise.all([fetch('data/articles.json?'+Date.now()).then(r=>r.json()),fetch('data/run_status.json?'+Date.now()).then(r=>r.json()),fetch('data/source_summary.json?'+Date.now()).then(r=>r.json())]);all=a;document.getElementById('sources').textContent=(ss.total||0).toLocaleString();fillSelect('country',all.map(x=>x.country));fillSelect('source',all.map(x=>x.source));document.getElementById('status').textContent=`最近一次云端巡检：${dt(st.finished_at)}；巡检来源 ${st.sources_scanned||0}，抓到候选 ${st.items_seen||0}，新增候选 ${st.items_new||0}，广义涉华入池 ${st.items_relevant||0}，抓取失败来源 ${st.errors||0}。${st.ai_enabled?'AI广义涉华判定已启用。':'未配置AI密钥，当前使用规则筛选。'}`;apply()}catch(e){document.getElementById('status').textContent='读取数据失败：'+e;}}
document.getElementById('go').onclick=apply;document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')apply()});document.getElementById('prev').onclick=()=>{if(page>1){page--;render();scrollTo(0,0)}};document.getElementById('next').onclick=()=>{if(page<Math.ceil(filtered.length/size)){page++;render();scrollTo(0,0)}};init();
</script></body></html>'''


def self_test():
    interests = {"profiles": {"Hungary": {"aliases": ["Hungary", "Hungarian", "Budapest"], "interests": ["中国制造业投资", "欧盟对华政策"], "entities": ["BYD Szeged", "CATL Debrecen", "Budapest-Belgrade railway"]}}}
    tests = [
        (RawItem("EU announces new restrictions on Chinese chip firms", "https://x/a", "X", source_country="European Union"), "direct"),
        (RawItem("Hungary's new government vows to restore relations with Brussels after election", "https://x/b", "Global", source_country="Global"), "potential"),
        (RawItem("Hungary wins dramatic football match in extra time", "https://x/c", "Global", source_country="Global"), "unrelated"),
        (RawItem("Budapest reviews subsidies for CATL Debrecen battery plant", "https://x/d", "Global", source_country="Global"), "direct"),
    ]
    for item, expected in tests:
        got = heuristic_decision(item, interests).relation
        if got != expected:
            raise AssertionError(f"self-test failed: {item.title} expected={expected} got={got}")
    print("self-test: 4/4 passed")


async def run():
    started = datetime.now(timezone.utc)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sources, interests = load_sources(), load_interests()
    log(f"start cloud crawl: {len(sources)} configured sources; AI={'on' if OPENAI_API_KEY else 'off'}")
    existing = load_existing()
    seen_urls = {r.get("canonical_url") or canonicalize_url(r.get("url", "")) for r in existing if r.get("url")}
    items, errors = await collect_all(sources)
    # de-duplicate the current crawl before classification
    unique = {}
    for item in items:
        canon = canonicalize_url(item.url)
        if not canon or canon in seen_urls:
            continue
        unique.setdefault(canon, item)
    new_items = list(unique.values())
    log(f"collected={len(items)}, new_candidates={len(new_items)}, source_errors={len(errors)}")
    relevant = classify_items(new_items, interests)
    now = datetime.now(timezone.utc)
    new_records = [article_record(item, decision, now) for item, decision in relevant]
    merged = merge_and_store(existing, new_records)
    ARTICLES_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "started_at": iso(started), "finished_at": iso(datetime.now(timezone.utc)), "sources_scanned": len(sources),
        "items_seen": len(items), "items_new": len(new_items), "items_relevant": len(new_records), "stored_articles": len(merged),
        "errors": len(errors), "error_samples": errors[:30], "ai_enabled": bool(OPENAI_API_KEY), "classifier": OPENAI_MODEL if OPENAI_API_KEY else "rules",
        "retention_days": RETENTION_DAYS, "site_max_articles": SITE_MAX,
    }
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    build_site(merged, status, sources)
    log(f"done: relevant_new={len(new_records)}, stored={len(merged)}")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test(); return
    self_test()
    status = asyncio.run(run())
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
