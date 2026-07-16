from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
import html
import re
import time

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.sdis38.fr/44-actualites.htm"
BASE_URL = "https://www.sdis38.fr/"
OUTPUT_FILE = Path("rss.xml")
MAX_ITEMS = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SDIS38-RSS/1.0; +https://github.com/remy1838/sdis38-rss)"
}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def xml_escape(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def parse_date(text: str) -> tuple[str, datetime]:
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
    if not match:
        now = datetime.now(timezone.utc)
        return "", now
    date_text = match.group(1)
    return date_text, datetime.strptime(date_text, "%d/%m/%Y").replace(tzinfo=timezone.utc)


def image_from_container(container) -> str:
    if container is None:
        return ""
    image = container.find("img")
    if image is None:
        return ""
    source = (
        image.get("src")
        or image.get("data-src")
        or image.get("data-original")
        or image.get("data-lazy-src")
        or ""
    )
    return urljoin(BASE_URL, source)


def article_metadata(session: requests.Session, article_url: str) -> tuple[str, str]:
    """Retourne une image et un résumé depuis la page de l'article."""
    try:
        response = session.get(article_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        image_url = ""
        for selector, attr in [
            ('meta[property="og:image"]', "content"),
            ('meta[name="twitter:image"]', "content"),
        ]:
            tag = soup.select_one(selector)
            if tag and tag.get(attr):
                image_url = urljoin(BASE_URL, tag.get(attr))
                break

        if not image_url:
            main = soup.select_one("main") or soup.select_one("article") or soup
            image_url = image_from_container(main)

        summary = ""
        description = soup.select_one('meta[name="description"]')
        if description and description.get("content"):
            summary = clean_text(description.get("content"))

        if not summary:
            heading = soup.find("h1")
            for paragraph in soup.find_all("p"):
                text = clean_text(paragraph.get_text(" ", strip=True))
                if len(text) >= 50 and (not heading or text != clean_text(heading.get_text(" ", strip=True))):
                    summary = text
                    break

        return image_url, summary[:500]
    except requests.RequestException as exc:
        print(f"Avertissement : impossible de lire {article_url}: {exc}")
        return "", ""


def extract_articles(page_html: str, session: requests.Session) -> list[dict[str, str | datetime]]:
    soup = BeautifulSoup(page_html, "html.parser")
    results: list[dict[str, str | datetime]] = []
    seen: set[str] = set()

    links = soup.select('a[href*="/actualite/"]')
    for link in links:
        article_url = urljoin(BASE_URL, link.get("href", ""))
        if not article_url or article_url in seen:
            continue

        full_text = clean_text(link.get_text(" ", strip=True))
        date_text, publication_date = parse_date(full_text)
        title = full_text
        if date_text:
            title = clean_text(full_text.split(date_text, 1)[0])

        if not title or len(title) < 5:
            continue

        container = link.find_parent(["li", "article", "section", "div"])
        container_text = clean_text(container.get_text(" ", strip=True)) if container else full_text
        description = container_text
        if title and title in description:
            description = description.replace(title, "", 1)
        if date_text and date_text in description:
            description = description.replace(date_text, "", 1)
        description = clean_text(description)[:500]

        image_url = image_from_container(container)
        remote_image, remote_summary = article_metadata(session, article_url)
        if remote_image:
            image_url = remote_image
        if remote_summary:
            description = remote_summary

        results.append(
            {
                "title": title,
                "url": article_url,
                "date": publication_date,
                "description": description,
                "image": image_url,
            }
        )
        seen.add(article_url)
        if len(results) >= MAX_ITEMS:
            break
        time.sleep(0.2)

    return results


def build_rss(articles: list[dict[str, str | datetime]]) -> str:
    items: list[str] = []

    for article in articles:
        title = xml_escape(str(article["title"]))
        url = xml_escape(str(article["url"]))
        description = xml_escape(str(article["description"]))
        publication_date = article["date"]
        assert isinstance(publication_date, datetime)
        image = str(article["image"])

        image_html = ""
        enclosure = ""
        media = ""
        if image:
            escaped_image = xml_escape(image)
            image_html = f'<p><img src="{escaped_image}" alt="{title}" /></p>'
            enclosure = f'<enclosure url="{escaped_image}" type="image/jpeg" />'
            media = f'<media:content url="{escaped_image}" medium="image" />'

        items.append(
            f"""
    <item>
      <title>{title}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{url}</guid>
      <pubDate>{format_datetime(publication_date)}</pubDate>
      <description><![CDATA[{image_html}<p>{description}</p>]]></description>
      {enclosure}
      {media}
    </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Actualités du SDIS 38</title>
    <link>{SOURCE_URL}</link>
    <description>Actualités officielles du Service départemental d'incendie et de secours de l'Isère</description>
    <language>fr-fr</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
    <ttl>60</ttl>
{''.join(items)}
  </channel>
</rss>
"""


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Téléchargement de {SOURCE_URL}")
    response = session.get(SOURCE_URL, timeout=90)
    response.raise_for_status()

    articles = extract_articles(response.text, session)
    if not articles:
        raise RuntimeError("Aucune actualité trouvée sur le site du SDIS 38.")

    OUTPUT_FILE.write_text(build_rss(articles), encoding="utf-8")
    print(f"Flux créé : {OUTPUT_FILE} ({len(articles)} actualités)")


if __name__ == "__main__":
    main()
