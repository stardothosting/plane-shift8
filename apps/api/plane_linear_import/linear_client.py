"""GraphQL client for the Linear API.

Handles authentication, pagination, and rate-limit back-off.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_PAGE_SIZE = 50
# Linear allows 1 500 complexity points / minute.  We stay conservative.
RATE_LIMIT_SLEEP_SECONDS = 10
MAX_RETRIES = 3


class LinearClientError(Exception):
    """Raised when the Linear API returns an error."""

    def __init__(self, errors: list[dict], query: str | None = None):
        self.errors = errors
        self.query = query
        messages = "; ".join(e.get("message", str(e)) for e in errors)
        super().__init__(f"Linear API error: {messages}")


class LinearClient:
    """Thin wrapper around Linear's public GraphQL API."""

    def __init__(self, api_key: str, *, timeout: float = 30.0):
        if not api_key:
            raise ValueError("LINEAR_API_KEY must be provided")
        self._client = httpx.Client(
            base_url=LINEAR_API_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LinearClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single GraphQL request with automatic retry on rate-limit."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(1, MAX_RETRIES + 1):
            resp = self._client.post("", json=payload)
            if resp.status_code == 429:
                logger.warning(
                    "Rate-limited by Linear (attempt %d/%d), sleeping %ds",
                    attempt,
                    MAX_RETRIES,
                    RATE_LIMIT_SLEEP_SECONDS,
                )
                time.sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            resp.raise_for_status()
            body = resp.json()
            if "errors" in body:
                raise LinearClientError(body["errors"], query=query)
            return body["data"]

        raise LinearClientError(
            [{"message": "Rate limit exceeded after retries"}], query=query
        )

    # ------------------------------------------------------------------
    # Paginated helpers
    # ------------------------------------------------------------------

    def paginate(
        self,
        query: str,
        connection_path: str,
        variables: dict[str, Any] | None = None,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Auto-paginate a Relay-style connection and return all nodes.

        *connection_path* is a dot-separated path from the top-level ``data``
        object to the connection, e.g. ``"teams"`` or ``"team.issues"``.
        """
        variables = dict(variables or {})
        variables.setdefault("first", page_size)
        all_nodes: list[dict[str, Any]] = []

        while True:
            data = self.execute(query, variables)
            obj = data
            for key in connection_path.split("."):
                obj = obj[key]

            nodes = obj.get("nodes", [])
            all_nodes.extend(nodes)

            page_info = obj.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                variables["after"] = page_info["endCursor"]
            else:
                break

        return all_nodes

    # ------------------------------------------------------------------
    # High-level data fetching
    # ------------------------------------------------------------------

    def fetch_organization(self) -> dict[str, Any]:
        query = """
        query {
          organization {
            id
            name
            urlKey
          }
        }
        """
        return self.execute(query)["organization"]

    def fetch_users(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          users(first: $first, after: $after, includeArchived: true) {
            nodes {
              id
              name
              displayName
              email
              active
              admin
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "users")

    def fetch_teams(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          teams(first: $first, after: $after) {
            nodes {
              id
              name
              key
              description
              states {
                nodes {
                  id
                  name
                  color
                  type
                  position
                }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "teams")

    def fetch_labels(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          issueLabels(first: $first, after: $after) {
            nodes {
              id
              name
              color
              parent { id }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "issueLabels")

    def fetch_issues_for_team(self, team_id: str) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String, $teamId: String!) {
          issues(
            first: $first,
            after: $after,
            filter: { team: { id: { eq: $teamId } } },
            includeArchived: true
          ) {
            nodes {
              id
              identifier
              title
              description
              descriptionState
              priority
              priorityLabel
              state { id name }
              assignee { id email }
              labels { nodes { id } }
              parent { id }
              createdAt
              updatedAt
              startedAt
              completedAt
              canceledAt
              dueDate
              estimate
              url
              relations {
                nodes {
                  id
                  type
                  relatedIssue { id }
                }
              }
              reactions {
                nodes {
                  id
                  emoji
                  user { id email }
                }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "issues", {"teamId": team_id})

    def fetch_comments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String, $issueId: ID!) {
          comments(
            first: $first,
            after: $after,
            filter: { issue: { id: { eq: $issueId } } }
          ) {
            nodes {
              id
              body
              createdAt
              updatedAt
              user { id email }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "comments", {"issueId": issue_id})

    def fetch_attachments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String, $issueId: ID!) {
          attachments(
            first: $first,
            after: $after,
            filter: { issue: { id: { eq: $issueId } } }
          ) {
            nodes {
              id
              title
              subtitle
              url
              metadata
              source { type }
              createdAt
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "attachments", {"issueId": issue_id})
