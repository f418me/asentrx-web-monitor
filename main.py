import logging
import os
import random
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()

log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, log_level_str, None)
if not isinstance(numeric_level, int):
    print(f"WARNING: Invalid LOG_LEVEL '{log_level_str}' in .env or environment. Defaulting to INFO.")
    configured_log_level = logging.INFO
else:
    configured_log_level = numeric_level

logging.basicConfig(level=configured_log_level, format="%(asctime)s - %(levelname)s - %(message)s")
logging.info(f"Logging level set to: {logging.getLevelName(configured_log_level)}")

PROCESSED_URLS_FILE = "processed_article_urls.txt"
PRESS_RELEASE_PATH_RE = re.compile(r"/newsevents/pressreleases/[a-z]+20\d{6}[a-z0-9]*\.htm$", re.IGNORECASE)
BLS_EMPLOYMENT_REPORT_RE = re.compile(r"THE EMPLOYMENT SITUATION\s*-\s*([A-Z]+\s+\d{4})", re.IGNORECASE)
BLS_CPI_REPORT_RE = re.compile(r"CONSUMER PRICE INDEX\s*-\s*([A-Z]+\s+\d{4})", re.IGNORECASE)
BLS_RELEASE_DATE_RE = re.compile(
    r"8:30\s*a\.m\.\s*\(ET\)\s*(?:Friday,\s*)?([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


def load_processed_urls() -> Set[str]:
    if not os.path.exists(PROCESSED_URLS_FILE):
        return set()
    with open(PROCESSED_URLS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_processed_url(url: str):
    with open(PROCESSED_URLS_FILE, "a") as f:
        f.write(url + "\n")


class WebMonitorPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID for this payload instance.")
    type: Literal["web-monitor", "truthsocial", "twitter"] = Field(default="web-monitor")
    url: Optional[str] = Field(default=None, description="The URL of the monitored article or source.")
    username: Optional[str] = Field(default=None, description="Optional username associated with the content.")
    content_id: Optional[str] = Field(default="", alias="content-id")
    content: str = Field(description="The main content of the message or data point.")
    ip: str = Field(description="The public IP of the monitor instance.")


def get_page_content(url: str) -> Optional[bytes]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    try:
        logging.debug(f"Fetching URL: {url}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.content
    except requests.exceptions.Timeout:
        logging.error(f"Request to {url} timed out.")
        return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Failed to connect to {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP error for {url}: {e.response.status_code} - {e.response.text[:100]}...")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"An unexpected error occurred fetching {url}: {e}")
        return None


def extract_recent_article_urls(html_content: bytes, base_url: str, count: int = 5) -> List[Dict[str, str]]:
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    recent_dev_heading = soup.find("h2", string=lambda text: text and "Recent Developments" in text.strip())
    if not recent_dev_heading:
        logging.info("Could not find the 'Recent Developments' heading (H2 tag). Trying generic press-release extraction.")
        return extract_press_release_index_urls(soup, base_url, count)

    heading_parent_row = recent_dev_heading.find_parent("div", class_=lambda c: c and "row" in c and "padded-row" in c)
    if not heading_parent_row:
        logging.warning("Could not find the parent row of the 'Recent Developments' heading.")
        return []

    articles_main_row = heading_parent_row.find_next_sibling("div", class_=lambda c: c and "row" in c)
    if not articles_main_row:
        logging.warning("Could not find the row containing articles (sibling of the heading row).")
        return []

    article_column = articles_main_row.find("div", class_="col-xs-12 col-sm-8")
    if not article_column:
        logging.warning("Could not find the column (col-xs-12 col-sm-8) with the article list.")
        return []

    article_ul = article_column.find("ul", class_="list-unstyled")
    if not article_ul:
        logging.warning("Could not find the article list (ul.list-unstyled).")
        return []

    recent_articles = []
    list_items = article_ul.find_all("li")
    for i, li in enumerate(list_items):
        if i >= count:
            break

        link_tag = li.find("a")
        if link_tag and link_tag.has_attr("href"):
            relative_url = link_tag["href"]
            absolute_url = urljoin(base_url, relative_url)
            title = link_tag.get_text(strip=True)
            recent_articles.append({"url": absolute_url, "title": title})
        else:
            logging.debug(f"Could not find a link in list item {i + 1}.")

    return recent_articles


def extract_press_release_index_urls(soup: BeautifulSoup, base_url: str, count: int = 5) -> List[Dict[str, str]]:
    container = soup.find("div", id="article") or soup.find("main") or soup.find("body")
    if not container:
        logging.warning("Could not find a suitable container for generic press-release extraction.")
        return []

    recent_articles: List[Dict[str, str]] = []
    seen_urls: Set[str] = set()

    for link_tag in container.find_all("a", href=True):
        href = link_tag["href"]
        absolute_url = urljoin(base_url, href)
        parsed_path = urlparse(absolute_url).path
        title = link_tag.get_text(" ", strip=True)

        if not title:
            continue
        if not PRESS_RELEASE_PATH_RE.search(parsed_path):
            continue
        if absolute_url in seen_urls:
            continue

        seen_urls.add(absolute_url)
        recent_articles.append({"url": absolute_url, "title": title})
        if len(recent_articles) >= count:
            break

    if recent_articles:
        logging.info(f"Generic fallback extracted {len(recent_articles)} press-release links.")
    else:
        logging.warning("Generic fallback did not find any press-release article links.")

    return recent_articles


def extract_article_content(html_content: bytes) -> Optional[str]:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")
    article_div = soup.find("div", id="article")

    if not article_div:
        possible_containers = [
            ("div", {"class": "col-md-12 mb-3"}),
            ("div", {"class": "col-md-8 offset-md-2 mb-3"}),
            "article",
            ("div", {"class": "container body-container"}),
        ]
        for tag_info in possible_containers:
            if isinstance(tag_info, str):
                temp_div = soup.find(tag_info)
            else:
                tag_name, attrs = tag_info
                temp_div = soup.find(tag_name, attrs)

            if temp_div and temp_div.get_text(strip=True) and len(temp_div.get_text(strip=True)) > 200:
                article_div = temp_div
                logging.debug(f"Fallback: Article content found in '{tag_info}'.")
                break

        if not article_div:
            logging.warning("Could not find the main content area of the article with standard selectors. Attempting body content.")
            body_content = soup.find("body")
            if body_content:
                for nav_selector in [
                    "nav",
                    ".navbar",
                    "header",
                    "footer",
                    ".t1_nav",
                    ".t2__offcanvas",
                    ".footer",
                    "aside",
                    ".skip-link",
                ]:
                    for tag in body_content.select(nav_selector):
                        tag.decompose()
                article_div = body_content
            else:
                return "Main content area of the article could not be identified."

    content_parts = []
    text_elements = article_div.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"])

    for elem in text_elements:
        if elem.name in ["script", "style"]:
            continue
        text = elem.get_text(strip=True)
        if text and len(text) > 10 and not any(
            nav_text in text.lower()
            for nav_text in [
                "skip to main content",
                "related links",
                "share this page",
                "page last updated",
                "contact us",
                "subscribe",
            ]
        ):
            content_parts.append(text)

    if not content_parts and article_div:
        content = article_div.get_text(separator="\n\n", strip=True)
    elif content_parts:
        content = "\n\n".join(content_parts)
    else:
        content = "Content could not be extracted or was empty."

    return content if content else "Content could not be extracted or was empty."


def extract_content_id_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    path_segment = os.path.basename(parsed_url.path)
    return os.path.splitext(path_segment)[0]


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def extract_bls_employment_report_metadata(html_content: bytes, page_url: str) -> Optional[Dict[str, str]]:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")
    full_text = soup.get_text("\n", strip=True)
    report_match = BLS_EMPLOYMENT_REPORT_RE.search(full_text)
    if not report_match:
        logging.warning("Could not identify the current BLS Employment Situation release label on the page.")
        return None

    report_label = " ".join(word.capitalize() for word in report_match.group(1).split())
    content = extract_article_content(html_content)
    if not content:
        logging.warning("Could not extract content from the BLS Employment Situation page.")
        return None

    content_id = f"bls-empsit-{_slugify(report_label)}"
    release_date_match = BLS_RELEASE_DATE_RE.search(full_text)
    if release_date_match:
        try:
            release_date = datetime.strptime(release_date_match.group(1), "%B %d, %Y").date().isoformat()
            content_id = f"{content_id}-released-{release_date}"
        except ValueError:
            logging.warning(f"Could not parse BLS release date '{release_date_match.group(1)}'.")

    return {
        "url": page_url,
        "title": f"Employment Situation - {report_label}",
        "content_id": content_id,
        "content": content,
    }


def extract_bls_cpi_report_metadata(html_content: bytes, page_url: str) -> Optional[Dict[str, str]]:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")
    full_text = soup.get_text("\n", strip=True)
    report_match = BLS_CPI_REPORT_RE.search(full_text)
    if not report_match:
        logging.warning("Could not identify the current BLS Consumer Price Index release label on the page.")
        return None

    report_label = " ".join(word.capitalize() for word in report_match.group(1).split())
    content = extract_article_content(html_content)
    if not content:
        logging.warning("Could not extract content from the BLS Consumer Price Index page.")
        return None

    content_id = f"bls-cpi-{_slugify(report_label)}"
    release_date_match = BLS_RELEASE_DATE_RE.search(full_text)
    if release_date_match:
        try:
            release_date = datetime.strptime(release_date_match.group(1), "%B %d, %Y").date().isoformat()
            content_id = f"{content_id}-released-{release_date}"
        except ValueError:
            logging.warning(f"Could not parse BLS release date '{release_date_match.group(1)}'.")

    return {
        "url": page_url,
        "title": f"Consumer Price Index - {report_label}",
        "content_id": content_id,
        "content": content,
    }


def send_data_to_webservice(payload_obj: WebMonitorPayload, webservice_url: str, auth_token: str):
    payload_dict = payload_obj.model_dump(by_alias=True)
    headers = {"Authorization": f"Bearer {auth_token}"}

    try:
        logging.info(
            f"Attempting to send data to {webservice_url} with payload (UUID: {payload_obj.uuid}, URL: {payload_obj.url})."
        )
        response = requests.post(webservice_url, json=payload_dict, headers=headers, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent data. Webservice responded with status: {response.status_code}")
        logging.debug(f"Webservice response content: {response.text}")
    except requests.exceptions.Timeout:
        logging.error(f"Request to webservice timed out after 10 seconds: {webservice_url}")
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Failed to connect to webservice: {webservice_url}. Error: {e}")
    except requests.exceptions.HTTPError as e:
        logging.error(f"Webservice returned an HTTP error: {e.response.status_code} - {e.response.text}. Response: {e.response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"An unexpected error occurred while sending data to webservice: {e}")


def _get_sleep_time(monitor_mode: str, prod_min: int, prod_max: int, normal_interval: int) -> int:
    if monitor_mode == "production":
        return random.randint(prod_min, prod_max)
    return normal_interval


def resolve_monitor_instance_id() -> str:
    return (
        os.getenv("FLY_PUBLIC_IP")
        or os.getenv("FLY_MACHINE_ID")
        or os.getenv("FLY_ALLOC_ID")
        or os.getenv("HOSTNAME")
        or "unknown-monitor-instance"
    )


def main():
    monitor_instance_id = resolve_monitor_instance_id()
    webservice_url = os.getenv("WEBSERVICE_URL")
    trade_engine_auth_token = os.getenv("TRADE_ENGINE_AUTH_TOKEN")
    monitor_target_kind = os.getenv("MONITOR_TARGET_KIND", "fomc").lower().strip()

    if monitor_target_kind == "nfp":
        monitor_base_url = os.getenv("MONITOR_BASE_URL", "https://www.bls.gov")
        monitor_main_page_path = os.getenv("MONITOR_MAIN_PAGE_PATH", "/news.release/empsit.htm")
    elif monitor_target_kind == "cpi":
        monitor_base_url = os.getenv("MONITOR_BASE_URL", "https://www.bls.gov")
        monitor_main_page_path = os.getenv("MONITOR_MAIN_PAGE_PATH", "/news.release/cpi.htm")
    else:
        monitor_base_url = os.getenv("MONITOR_BASE_URL", "https://www.federalreserve.gov")
        monitor_main_page_path = os.getenv("MONITOR_MAIN_PAGE_PATH", "/newsevents/pressreleases/2026-press-fomc.htm")

    monitor_interval_seconds_str = os.getenv("MONITOR_INTERVAL_SECONDS", "300")
    monitor_recent_articles_count_str = os.getenv("MONITOR_RECENT_ARTICLES_COUNT", "5")
    monitor_keyword = os.getenv("MONITOR_KEYWORD", "").lower().strip()
    monitor_title_keyword = os.getenv("MONITOR_TITLE_KEYWORD", "").lower().strip()
    monitor_mode = os.getenv("MONITOR_MODE", "normal").lower().strip()
    monitor_prod_min_seconds_str = os.getenv("MONITOR_PROD_MIN_SECONDS", "10")
    monitor_prod_max_seconds_str = os.getenv("MONITOR_PROD_MAX_SECONDS", "20")
    monitor_bootstrap_skip_existing = os.getenv("MONITOR_BOOTSTRAP_SKIP_EXISTING", "true").lower().strip() in {"1", "true", "yes", "on"}

    if not os.getenv("FLY_PUBLIC_IP"):
        logging.warning(
            "FLY_PUBLIC_IP environment variable not set. "
            f"Using monitor instance identifier '{monitor_instance_id}' in payload IP field."
        )
    if not webservice_url:
        logging.error("Error: WEBSERVICE_URL environment variable not set. Exiting.")
        return
    if not trade_engine_auth_token:
        logging.error("Error: TRADE_ENGINE_AUTH_TOKEN environment variable not set. Exiting.")
        return
    if monitor_target_kind not in {"fomc", "nfp", "cpi"}:
        logging.error(f"Error: MONITOR_TARGET_KIND must be 'fomc', 'nfp', or 'cpi'. Got '{monitor_target_kind}'. Exiting.")
        return

    try:
        monitor_interval_seconds = int(monitor_interval_seconds_str)
        if monitor_interval_seconds <= 0:
            raise ValueError
    except ValueError:
        logging.error(f"Error: MONITOR_INTERVAL_SECONDS must be a positive integer. Got '{monitor_interval_seconds_str}'. Exiting.")
        return

    try:
        monitor_recent_articles_count = int(monitor_recent_articles_count_str)
        if monitor_recent_articles_count <= 0:
            raise ValueError
    except ValueError:
        logging.error(f"Error: MONITOR_RECENT_ARTICLES_COUNT must be a positive integer. Got '{monitor_recent_articles_count_str}'. Exiting.")
        return

    valid_modes = {"normal", "production"}
    if monitor_mode not in valid_modes:
        logging.error(f"Error: MONITOR_MODE must be one of {valid_modes}. Got '{monitor_mode}'. Exiting.")
        return

    try:
        monitor_prod_min_seconds = int(monitor_prod_min_seconds_str)
        monitor_prod_max_seconds = int(monitor_prod_max_seconds_str)
        if monitor_prod_min_seconds <= 0 or monitor_prod_max_seconds < monitor_prod_min_seconds:
            raise ValueError
    except ValueError:
        logging.error("Error: MONITOR_PROD_MIN_SECONDS and MONITOR_PROD_MAX_SECONDS must be positive integers and MAX >= MIN. Exiting.")
        return

    monitor_full_main_page_url = urljoin(monitor_base_url, monitor_main_page_path)

    logging.info(f"Monitor configured to send instance identifier '{monitor_instance_id}' to '{webservice_url}'.")
    logging.info(f"Monitoring target kind: '{monitor_target_kind}'.")
    logging.info(f"Webpage monitoring: Base URL='{monitor_base_url}', Main Page='{monitor_full_main_page_url}'.")
    if monitor_mode == "production":
        logging.info(f"Checking target every {monitor_prod_min_seconds}-{monitor_prod_max_seconds} seconds (random).")
    else:
        logging.info(f"Checking target every {monitor_interval_seconds} seconds.")
    if monitor_target_kind == "fomc":
        logging.info(f"FOMC mode inspects the {monitor_recent_articles_count} most recent extracted links per cycle.")
    if monitor_title_keyword:
        logging.info(f"Filtering new articles by TITLE keyword: '{monitor_title_keyword}'.")
    else:
        logging.info("No title keyword filter applied.")
    if monitor_keyword:
        logging.info(f"Filtering new articles by CONTENT keyword: '{monitor_keyword}'.")
    else:
        logging.info("No content keyword filter applied.")

    processed_urls = load_processed_urls()
    logging.info(f"Loaded {len(processed_urls)} previously processed article URLs.")
    bootstrap_pending = monitor_bootstrap_skip_existing and len(processed_urls) == 0
    if bootstrap_pending:
        logging.info(
            "Bootstrap protection is enabled and no processed URLs were found. Existing visible items from the first successful scrape will be marked as seen and not sent."
        )

    while True:
        logging.info(f"\n--- Starting website check ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
        logging.info(f"Monitor instance identifier is: {monitor_instance_id}")

        main_page_html = get_page_content(monitor_full_main_page_url)
        sleep_time = _get_sleep_time(
            monitor_mode,
            monitor_prod_min_seconds,
            monitor_prod_max_seconds,
            monitor_interval_seconds,
        )
        if not main_page_html:
            logging.error(f"Could not load main page: {monitor_full_main_page_url}. Retrying in {sleep_time}s.")
            time.sleep(sleep_time)
            continue

        if monitor_target_kind == "nfp":
            release_info = extract_bls_employment_report_metadata(main_page_html, monitor_full_main_page_url)
            if not release_info:
                logging.info(f"Could not parse the current BLS Employment Situation page. Retrying in {sleep_time}s.")
                time.sleep(sleep_time)
                continue

            seen_key = release_info["content_id"]
            article_title = release_info["title"]
            logging.info(f"Detected direct release snapshot: '{article_title}' ({seen_key}).")

            if bootstrap_pending:
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                bootstrap_pending = False
                logging.info("Bootstrap protection marked the current BLS release snapshot as processed. Waiting for a newer release before sending anything downstream.")
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            if seen_key in processed_urls:
                logging.info("No new matching BLS release found in this cycle.")
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            if monitor_title_keyword and monitor_title_keyword not in article_title.lower():
                logging.info(f"Direct release title '{article_title}' does NOT contain keyword '{monitor_title_keyword}'. Not processing.")
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            article_content = release_info["content"]
            if monitor_keyword and monitor_keyword not in article_content.lower():
                logging.info(f"Direct release content does NOT contain keyword '{monitor_keyword}'. Not sending.")
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            payload = WebMonitorPayload(
                ip=monitor_instance_id,
                url=release_info["url"],
                content_id=release_info["content_id"],
                content=article_content,
            )
            logging.info(f"Sending direct release snapshot (ID: {release_info['content_id']}, Title: '{article_title}') to webservice.")
            send_data_to_webservice(payload, webservice_url, trade_engine_auth_token)
            save_processed_url(seen_key)
            processed_urls.add(seen_key)
            logging.info(f"Release snapshot {seen_key} marked as processed.")
            logging.info(f"Waiting for {sleep_time} seconds until next check...")
            time.sleep(sleep_time)
            continue

        if monitor_target_kind == "cpi":
            release_info = extract_bls_cpi_report_metadata(main_page_html, monitor_full_main_page_url)
            if not release_info:
                logging.info(f"Could not parse the current BLS Consumer Price Index page. Retrying in {sleep_time}s.")
                time.sleep(sleep_time)
                continue

            seen_key = release_info["content_id"]
            article_title = release_info["title"]
            logging.info(f"Detected direct release snapshot: '{article_title}' ({seen_key}).")

            if bootstrap_pending:
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                bootstrap_pending = False
                logging.info("Bootstrap protection marked the current BLS release snapshot as processed. Waiting for a newer release before sending anything downstream.")
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            if seen_key in processed_urls:
                logging.info("No new matching BLS release found in this cycle.")
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            if monitor_title_keyword and monitor_title_keyword not in article_title.lower():
                logging.info(f"Direct release title '{article_title}' does NOT contain keyword '{monitor_title_keyword}'. Not processing.")
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            article_content = release_info["content"]
            if monitor_keyword and monitor_keyword not in article_content.lower():
                logging.info(f"Direct release content does NOT contain keyword '{monitor_keyword}'. Not sending.")
                save_processed_url(seen_key)
                processed_urls.add(seen_key)
                logging.info(f"Waiting for {sleep_time} seconds until next check...")
                time.sleep(sleep_time)
                continue

            payload = WebMonitorPayload(
                ip=monitor_instance_id,
                url=release_info["url"],
                content_id=release_info["content_id"],
                content=article_content,
            )
            logging.info(f"Sending direct release snapshot (ID: {release_info['content_id']}, Title: '{article_title}') to webservice.")
            send_data_to_webservice(payload, webservice_url, trade_engine_auth_token)
            save_processed_url(seen_key)
            processed_urls.add(seen_key)
            logging.info(f"Release snapshot {seen_key} marked as processed.")
            logging.info(f"Waiting for {sleep_time} seconds until next check...")
            time.sleep(sleep_time)
            continue

        recent_articles = extract_recent_article_urls(main_page_html, monitor_base_url, monitor_recent_articles_count)
        if not recent_articles:
            logging.info(f"No recent articles found or could not parse. Retrying in {sleep_time}s.")
            time.sleep(sleep_time)
            continue

        logging.info(f"Found {len(recent_articles)} recent articles on main page.")
        if bootstrap_pending:
            for article_info in recent_articles:
                article_url = article_info["url"]
                if article_url in processed_urls:
                    continue
                save_processed_url(article_url)
                processed_urls.add(article_url)
            bootstrap_pending = False
            logging.info("Bootstrap protection marked currently visible articles as processed. Waiting for newly published links before sending anything downstream.")
            logging.info(f"Waiting for {sleep_time} seconds until next check...")
            time.sleep(sleep_time)
            continue

        new_article_found_and_processed = False
        for article_info in recent_articles:
            article_url = article_info["url"]
            article_title = article_info["title"]

            if article_url in processed_urls:
                logging.debug(f"Article '{article_title}' ({article_url}) already processed.")
                continue

            logging.info(f"NEW article identified: '{article_title}' ({article_url})")
            if monitor_title_keyword and monitor_title_keyword not in article_title.lower():
                logging.info(f"New article '{article_title}' title does NOT contain keyword '{monitor_title_keyword}'. Not processing.")
                save_processed_url(article_url)
                processed_urls.add(article_url)
                continue

            article_html = get_page_content(article_url)
            if not article_html:
                logging.error(f"Could not retrieve content for new article: {article_url}. Skipping.")
                continue

            article_content = extract_article_content(article_html)
            if not article_content:
                logging.error(f"Could not extract content from new article: {article_url}. Skipping.")
                save_processed_url(article_url)
                processed_urls.add(article_url)
                continue

            if monitor_keyword and monitor_keyword not in article_content.lower():
                logging.info(f"New article '{article_title}' content does NOT contain keyword '{monitor_keyword}'. Not sending.")
                save_processed_url(article_url)
                processed_urls.add(article_url)
                continue

            logging.debug(
                f"\n--- Extracted Article Content for '{article_title}' ({article_url}) ---\n{article_content}\n--- End Article Content ---"
            )
            content_id = extract_content_id_from_url(article_url)
            payload = WebMonitorPayload(
                ip=monitor_instance_id,
                url=article_url,
                content_id=content_id,
                content=article_content,
            )
            logging.info(f"Sending new article data (ID: {content_id}, Title: '{article_title}') to webservice.")
            send_data_to_webservice(payload, webservice_url, trade_engine_auth_token)
            new_article_found_and_processed = True
            save_processed_url(article_url)
            processed_urls.add(article_url)
            logging.info(f"Article {article_url} marked as processed.")

        if not new_article_found_and_processed:
            logging.info("No new matching articles found in this cycle.")

        logging.info(f"Waiting for {sleep_time} seconds until next check...")
        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("\nProgram terminated by user (Ctrl+C).")
    except Exception as e:
        logging.critical(f"An unhandled error occurred: {e}", exc_info=True)
