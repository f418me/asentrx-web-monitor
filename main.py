import os
import time
import requests
import logging
import uuid
from typing import Optional, Literal, Set, List, Dict
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# Load environment variables FIRST to ensure LOG_LEVEL is available for logging configuration
load_dotenv()

# Get log level string from environment variable, default to "INFO"
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()

# Convert string log level to numeric level using getattr
numeric_level = getattr(logging, log_level_str, None)

# Validate the obtained level. If invalid, fallback to INFO and warn.
if not isinstance(numeric_level, int):
    print(f"WARNING: Invalid LOG_LEVEL '{log_level_str}' in .env or environment. Defaulting to INFO.")
    configured_log_level = logging.INFO
else:
    configured_log_level = numeric_level

# Configure logging
logging.basicConfig(level=configured_log_level, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info(f"Logging level set to: {logging.getLevelName(configured_log_level)}")

# --- Global Configuration and File Access Helpers ---
PROCESSED_URLS_FILE = "processed_article_urls.txt"


def load_processed_urls() -> Set[str]:
    """Loads URLs of already processed articles from a file."""
    if not os.path.exists(PROCESSED_URLS_FILE):
        return set()
    with open(PROCESSED_URLS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_processed_url(url: str):
    """Saves a URL to the file of processed articles."""
    with open(PROCESSED_URLS_FILE, "a") as f:
        f.write(url + "\n")


# --- Pydantic Model for the Payload ---
class WebMonitorPayload(BaseModel):
    """
    Pydantic model for the data sent to the webservice.
    Defines structure, types, default values, and aliases for JSON fields.
    """
    uuid: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this payload instance."
    )
    type: Literal["web-monitor", "truthsocial", "twitter"] = Field(
        default="web-monitor",
        description="The type of monitor or source from which the data originates."
    )
    url: Optional[str] = Field(
        default=None,
        description="The URL of the monitored article or source."
    )
    username: Optional[str] = Field(
        default=None,
        description="Optional username associated with the content."
    )
    content_id: Optional[str] = Field(
        default="",
        alias="content-id",  # Important for JSON output
        description="Optional ID for the specific content (e.g., ID of a tweet or post, here article ID from URL)."
    )
    content: str = Field(
        description="The main content of the message or data point."
    )
    ip: str = Field(
        description="The IP address from which the data is sent (here, the public IP of the Fly.io container)."
    )


# --- Web Scraper Functions ---

def get_page_content(url: str) -> Optional[bytes]:
    """Retrieves the HTML content of a URL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        logging.debug(f"Fetching URL: {url}")
        response = requests.get(url, headers=headers, timeout=15)  # Increased timeout for external pages
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
    """
    Extracts the URLs and titles of the latest articles from the 'Recent Developments' section.
    Returns a list of dictionaries: [{'url': '...', 'title': '...'}]
    """
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    recent_dev_heading = soup.find('h2', string=lambda text: text and 'Recent Developments' in text.strip())
    if not recent_dev_heading:
        logging.warning("Could not find the 'Recent Developments' heading (H2 tag).")
        return []

    heading_parent_row = recent_dev_heading.find_parent('div', class_=lambda c: c and 'row' in c and 'padded-row' in c)
    if not heading_parent_row:
        logging.warning("Could not find the parent row of the 'Recent Developments' heading.")
        return []

    articles_main_row = heading_parent_row.find_next_sibling('div', class_=lambda c: c and 'row' in c)
    if not articles_main_row:
        logging.warning("Could not find the row containing articles (sibling of the heading row).")
        return []

    article_column = articles_main_row.find('div', class_='col-xs-12 col-sm-8')
    if not article_column:
        logging.warning("Could not find the column (col-xs-12 col-sm-8) with the article list.")
        return []

    article_ul = article_column.find('ul', class_='list-unstyled')
    if not article_ul:
        logging.warning("Could not find the article list (ul.list-unstyled).")
        return []

    recent_articles = []
    list_items = article_ul.find_all('li')
    for i, li in enumerate(list_items):
        if i >= count:
            break

        link_tag = li.find('a')
        if link_tag and link_tag.has_attr('href'):
            relative_url = link_tag['href']
            absolute_url = urljoin(base_url, relative_url)
            title = link_tag.get_text(strip=True)
            recent_articles.append({'url': absolute_url, 'title': title})
        else:
            logging.debug(f"Could not find a link in list item {i + 1}.")

    return recent_articles


def extract_article_content(html_content: bytes) -> Optional[str]:
    """Extracts the main content of an article."""
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')

    article_div = soup.find('div', id='article')

    if not article_div:
        possible_containers = [
            ('div', {'class': 'col-md-12 mb-3'}),
            ('div', {'class': 'col-md-8 offset-md-2 mb-3'}),
            'article',
            ('div', {'class': 'container body-container'}),
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
            logging.warning(
                "Could not find the main content area of the article with standard selectors. Attempting body content.")
            body_content = soup.find('body')
            if body_content:
                for nav_selector in ['nav', '.navbar', 'header', 'footer', '.t1_nav', '.t2__offcanvas', '.footer',
                                     'aside', '.skip-link']:
                    for tag in body_content.select(nav_selector):
                        tag.decompose()
                article_div = body_content
            else:
                return "Main content area of the article could not be identified."

    content_parts = []
    text_elements = article_div.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote'])

    for elem in text_elements:
        if elem.name in ['script', 'style']:
            continue
        text = elem.get_text(strip=True)
        if text and len(text) > 10 and not any(nav_text in text.lower() for nav_text in
                                               ["skip to main content", "related links", "share this page",
                                                "page last updated", "contact us", "subscribe"]):
            content_parts.append(text)

    if not content_parts and article_div:
        content = article_div.get_text(separator='\n\n', strip=True)
    elif content_parts:
        content = "\n\n".join(content_parts)
    else:
        content = "Content could not be extracted or was empty."

    return content if content else "Content could not be extracted or was empty."


def extract_content_id_from_url(url: str) -> str:
    """Extracts an ID from the URL, e.g., the filename without extension."""
    parsed_url = urlparse(url)
    path_segment = os.path.basename(parsed_url.path)
    content_id = os.path.splitext(path_segment)[0]
    return content_id


# --- Webservice Communication Function ---
def send_data_to_webservice(payload_obj: WebMonitorPayload, webservice_url: str):
    """
    Constructs the JSON payload and sends it to the specified webservice URL.
    Logs success or failure.
    """
    payload_dict = payload_obj.model_dump(by_alias=True)

    try:
        logging.info(
            f"Attempting to send data to {webservice_url} with payload (UUID: {payload_obj.uuid}, URL: {payload_obj.url}).")
        response = requests.post(webservice_url, json=payload_dict, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent data. Webservice responded with status: {response.status_code}")
        logging.debug(f"Webservice response content: {response.text}")
    except requests.exceptions.Timeout:
        logging.error(f"Request to webservice timed out after 10 seconds: {webservice_url}")
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Failed to connect to webservice: {webservice_url}. Error: {e}")
    except requests.exceptions.HTTPError as e:
        logging.error(
            f"Webservice returned an HTTP error: {e.response.status_code} - {e.response.text}. Response: {e.response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"An unexpected error occurred while sending data to webservice: {e}")


# --- Main Logic ---

def main():
    """
    Main function to load environment variables and periodically send data.
    """
    # --- Configuration from Environment Variables ---
    fly_public_ip = os.getenv("FLY_PUBLIC_IP")
    webservice_url = os.getenv("WEBSERVICE_URL")

    monitor_base_url = os.getenv("MONITOR_BASE_URL", "https://www.federalreserve.gov")
    monitor_main_page_path = os.getenv("MONITOR_MAIN_PAGE_PATH", "/")
    monitor_interval_seconds_str = os.getenv("MONITOR_INTERVAL_SECONDS", "300")
    monitor_recent_articles_count_str = os.getenv("MONITOR_RECENT_ARTICLES_COUNT", "5")
    monitor_keyword = os.getenv("MONITOR_KEYWORD", "").lower().strip()
    monitor_title_keyword = os.getenv("MONITOR_TITLE_KEYWORD", "").lower().strip()

    # Validate essential environment variables
    if not fly_public_ip:
        logging.error("Error: FLY_PUBLIC_IP environment variable not set. Exiting.")
        return
    if not webservice_url:
        logging.error("Error: WEBSERVICE_URL environment variable not set. Exiting.")
        return

    try:
        monitor_interval_seconds = int(monitor_interval_seconds_str)
        if monitor_interval_seconds <= 0:
            raise ValueError("Monitor interval must be a positive number.")
    except ValueError:
        logging.error(
            f"Error: MONITOR_INTERVAL_SECONDS must be a positive integer. Got '{monitor_interval_seconds_str}'. Exiting.")
        return

    try:
        monitor_recent_articles_count = int(monitor_recent_articles_count_str)
        if monitor_recent_articles_count <= 0:
            raise ValueError("Monitor recent articles count must be a positive number.")
    except ValueError:
        logging.error(
            f"Error: MONITOR_RECENT_ARTICLES_COUNT must be a positive integer. Got '{monitor_recent_articles_count_str}'. Exiting.")
        return

    monitor_full_main_page_url = urljoin(monitor_base_url, monitor_main_page_path)

    logging.info(f"Monitor configured to send IP '{fly_public_ip}' to '{webservice_url}'.")
    logging.info(f"Webpage monitoring: Base URL='{monitor_base_url}', Main Page='{monitor_full_main_page_url}'.")
    logging.info(
        f"Checking {monitor_recent_articles_count} most recent articles every {monitor_interval_seconds} seconds.")
    if monitor_title_keyword:
        logging.info(f"Filtering new articles by TITLE keyword: '{monitor_title_keyword}'.")
    else:
        logging.info("No title keyword filter applied.")
    if monitor_keyword:
        logging.info(f"Filtering new articles by CONTENT keyword: '{monitor_keyword}'.")
    else:
        logging.info("No content keyword filter applied.")

    # Load previously processed URLs at startup
    processed_urls = load_processed_urls()
    logging.info(f"Loaded {len(processed_urls)} previously processed article URLs.")

    # --- Main Loop ---
    while True:
        logging.info(f"\n--- Starting website check ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
        logging.info(f"Public IP is: {fly_public_ip}")

        # Step 1: Retrieve main page and extract latest article URLs
        main_page_html = get_page_content(monitor_full_main_page_url)
        if not main_page_html:
            logging.error(
                f"Could not load main page: {monitor_full_main_page_url}. Retrying in {monitor_interval_seconds}s.")
            time.sleep(monitor_interval_seconds)
            continue

        recent_articles = extract_recent_article_urls(main_page_html, monitor_base_url, monitor_recent_articles_count)

        if not recent_articles:
            logging.info(f"No recent articles found or could not parse. Retrying in {monitor_interval_seconds}s.")
            time.sleep(monitor_interval_seconds)
            continue

        logging.info(f"Found {len(recent_articles)} recent articles on main page.")

        # Step 2: Check each recent article
        new_article_found_and_processed = False
        for article_info in recent_articles:
            article_url = article_info['url']
            article_title = article_info['title']

            if article_url in processed_urls:
                logging.debug(f"Article '{article_title}' ({article_url}) already processed.")
                continue

            logging.info(f"NEW article identified: '{article_title}' ({article_url})")

            # Step 3: Title keyword filter (if provided)
            if monitor_title_keyword and monitor_title_keyword not in article_title.lower():
                logging.info(
                    f"New article '{article_title}' title does NOT contain keyword '{monitor_title_keyword}'. Not processing.")
                save_processed_url(article_url)
                processed_urls.add(article_url)
                continue

            # Step 4: Retrieve content of the new article
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

            # Step 5: Content keyword filter (if provided)
            if monitor_keyword and monitor_keyword not in article_content.lower():
                logging.info(
                    f"New article '{article_title}' content does NOT contain keyword '{monitor_keyword}'. Not sending.")
                save_processed_url(article_url)
                processed_urls.add(article_url)
                continue

            # Log the full article content in DEBUG mode
            logging.debug(
                f"\n--- Extracted Article Content for '{article_title}' ({article_url}) ---\n{article_content}\n--- End Article Content ---")

            # Step 6: Create and send payload
            content_id = extract_content_id_from_url(article_url)

            payload = WebMonitorPayload(
                ip=fly_public_ip,
                url=article_url,
                content_id=content_id,
                content=article_content
            )

            logging.info(f"Sending new article data (ID: {content_id}, Title: '{article_title}') to webservice.")
            send_data_to_webservice(payload, webservice_url)
            new_article_found_and_processed = True

            # Step 7: Mark article as processed
            save_processed_url(article_url)
            processed_urls.add(article_url)
            logging.info(f"Article {article_url} marked as processed.")

        if not new_article_found_and_processed:
            logging.info("No new matching articles found in this cycle.")

        logging.info(f"Waiting for {monitor_interval_seconds} seconds until next check...")
        time.sleep(monitor_interval_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("\nProgram terminated by user (Ctrl+C).")
    except Exception as e:
        logging.critical(f"An unhandled error occurred: {e}", exc_info=True)