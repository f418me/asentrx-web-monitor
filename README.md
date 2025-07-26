> **Note:** This service is a component of the [aSentrX Project](https://github.com/f418me/aSentrX). Please see the main repository for a complete architectural overview.

# aSentrix Web Monitor

This service periodically scrapes a configured website for new articles. When a new article matching specific keywords is found, its content is extracted and sent to a configurable webhook URL for further processing.

## Features

*   **Configurable Interval**: Send data every X seconds (default 60s), configurable via `.env`.
*   **Dynamic IP**: Uses a `FLY_PUBLIC_IP` environment variable as the source IP.
*   **JSON Payload**: Sends a JSON object with `name`, `ip`, and `content` fields.
*   **HTTPS Support**: Designed to send data to HTTPS endpoints.
*   **Error Logging**: Logs messages if the webservice is unreachable or responds with errors.
*   **Containerized**: Easy deployment using Docker and Docker Compose.
*   **Poetry**: Manages Python dependencies efficiently.

## Prerequisites

Before you begin, ensure you have the following installed:

*   **Git**: For cloning the repository.
*   **Docker**: [Install Docker Desktop](https://www.docker.com/products/docker-desktop)
*   **Docker Compose**: Usually comes bundled with Docker Desktop.

## Setup and Running

Follow these steps to get the `asentrx-web-monitor` up and running:

### 1. Clone the Repository

First, clone this repository to your local machine:

```bash
git clone https://github.com/your-username/asentrx-web-monitor.git
cd asentrx-web-monitor
```

### Modes

Use the following environment variables to control the polling interval:

* `MONITOR_MODE` – set to `production` for faster checks, otherwise `normal`.
* `MONITOR_INTERVAL_SECONDS` – interval in seconds when in normal mode.
* `MONITOR_PROD_MIN_SECONDS` and `MONITOR_PROD_MAX_SECONDS` – random interval range used in production mode.