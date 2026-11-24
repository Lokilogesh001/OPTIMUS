"""core/marketplace/sources.py — Multi-source plugin and MCP server discovery engine.

Implements an SSRF-hardened discovery harvester for:
- Official MCP Registry
- GitHub Topics (mcp-server, optimus-plugin)
- Glama MCP Registry
- Smithery MCP Registry
- PyPI JSON API

All network access is guarded against SSRF, blocking private IP spaces, loopbacks,
and link-local metadata endpoints (AWS/GCP/Azure 169.254.169.254).
All untrusted text from remote registries is encapsulated in nonced XML tags.
"""
from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import secrets
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("optimus.marketplace.sources")

# RFC 1918, RFC 3927, Loopback, Carrier-Grade NAT, Multicast, Reserved
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


class SSRFSecurityViolation(Exception):
    """Raised when an outbound HTTP request targets a private, loopback, or cloud-metadata IP."""


class MarketplaceSource(str, Enum):
    OFFICIAL_MCP = "official_mcp"
    GITHUB = "github"
    GLAMA = "glama"
    SMITHERY = "smithery"
    PYPI = "pypi"
    LOCAL_BUILTIN = "local_builtin"


class PluginExecutionType(str, Enum):
    MCP_SERVER_STDIO = "mcp_server_stdio"
    MCP_SERVER_HTTP = "mcp_server_http"
    ISOLATED_CLI_PLUGIN = "isolated_cli_plugin"
    BUILTIN_NATIVE_PLUGIN = "builtin_native_plugin"


@dataclass
class DiscoveredPlugin:
    """Descriptor for a discovered marketplace item."""
    id: str
    name: str
    description: str
    source: MarketplaceSource
    execution_type: PluginExecutionType
    version: str = "1.0.0"
    repository_url: str = ""
    author: str = ""
    license: str = "Unknown"
    tags: List[str] = field(default_factory=list)
    download_url: str = ""
    integrity_hash: str = ""
    command: List[str] = field(default_factory=list)
    endpoint_url: str = ""
    environment_vars: Dict[str, str] = field(default_factory=dict)
    declared_permissions: List[str] = field(default_factory=list)
    untrusted_payload: str = ""
    nonce: str = ""
    is_verified: bool = False
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source.value,
            "execution_type": self.execution_type.value,
            "version": self.version,
            "repository_url": self.repository_url,
            "author": self.author,
            "license": self.license,
            "tags": self.tags,
            "download_url": self.download_url,
            "integrity_hash": self.integrity_hash,
            "command": self.command,
            "endpoint_url": self.endpoint_url,
            "environment_vars": self.environment_vars,
            "declared_permissions": self.declared_permissions,
            "nonce": self.nonce,
            "is_verified": self.is_verified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiscoveredPlugin:
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            source=MarketplaceSource(data.get("source", MarketplaceSource.OFFICIAL_MCP.value)),
            execution_type=PluginExecutionType(data.get("execution_type", PluginExecutionType.MCP_SERVER_STDIO.value)),
            version=data.get("version", "1.0.0"),
            repository_url=data.get("repository_url", ""),
            author=data.get("author", ""),
            license=data.get("license", "Unknown"),
            tags=data.get("tags", []),
            download_url=data.get("download_url", ""),
            integrity_hash=data.get("integrity_hash", ""),
            command=data.get("command", []),
            endpoint_url=data.get("endpoint_url", ""),
            environment_vars=data.get("environment_vars", {}),
            declared_permissions=data.get("declared_permissions", []),
            nonce=data.get("nonce", ""),
            is_verified=data.get("is_verified", False),
            raw_metadata=data.get("raw_metadata", {}),
        )


class SSRFHardenedClient:
    """HTTP client that validates every destination IP and redirect target against private networks."""

    def __init__(self, timeout_seconds: float = 10.0, user_agent: str = "Optimus-Marketplace-Harvester/2.0"):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def validate_url(self, url: str) -> str:
        """Parse URL and resolve hostname, checking against BLOCKED_IP_NETWORKS. Returns primary validated IP."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SSRFSecurityViolation(f"Unsupported URI scheme: {parsed.scheme}")

        hostname = parsed.hostname
        if not hostname:
            raise SSRFSecurityViolation("Empty or invalid hostname in URL")

        # Resolve IP addresses
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise SSRFSecurityViolation(f"Failed to resolve hostname '{hostname}': {e}") from e

        if not addr_info:
            raise SSRFSecurityViolation(f"No IP addresses resolved for hostname '{hostname}'")

        validated_ip: Optional[str] = None
        for entry in addr_info:
            ip_str = entry[4][0]
            ip_obj = ipaddress.ip_address(ip_str)

            # Check if within blocked networks
            for network in BLOCKED_IP_NETWORKS:
                if ip_obj in network:
                    logger.warning(
                        "SSRF violation blocked: '%s' resolved to forbidden IP '%s' in %s",
                        hostname, ip_str, network
                    )
                    raise SSRFSecurityViolation(
                        f"SSRF violation: target IP {ip_str} belongs to blocked subnet {network}"
                    )

            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                raise SSRFSecurityViolation(f"SSRF violation: target IP {ip_str} is reserved/private")

            if validated_ip is None:
                validated_ip = ip_str

        return validated_ip or ""

    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        """Fetch URL with SSRF protection and return parsed JSON."""
        self.validate_url(url)

        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, headers=req_headers, method="GET")

        # Custom opener to intercept and validate redirects
        class SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
            def __init__(self, parent_client: SSRFHardenedClient):
                self.parent_client = parent_client
                super().__init__()

            def redirect_request(self, req, fp, code, msg, headers, newurl):
                # Validate redirect target
                self.parent_client.validate_url(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(SSRFRedirectHandler(self))
        with opener.open(req, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_data = response.read()
            text_data = raw_data.decode("utf-8", errors="replace")
            return json.loads(text_data)


def encapsulate_untrusted_text(content: str, source: str) -> tuple[str, str]:
    """Encapsulates untrusted external metadata inside a nonced XML tag with HTML escaping."""
    nonce = secrets.token_hex(8)
    escaped = html.escape(content)
    tagged = f'<untrusted_data source="{source}" nonce="{nonce}">\n{escaped}\n</untrusted_data>'
    return tagged, nonce


class MarketplaceHarvester:
    """Coordinates multi-source discovery, normalization, and untrusted payload demarcations."""

    def __init__(self, http_client: Optional[SSRFHardenedClient] = None):
        self.http_client = http_client or SSRFHardenedClient()
        self._mock_sources: Dict[MarketplaceSource, List[Dict[str, Any]]] = {}

    def set_mock_source_data(self, source: MarketplaceSource, items: List[Dict[str, Any]]) -> None:
        """Allows testing or offline operation with deterministic fixtures."""
        self._mock_sources[source] = items

    def harvest_all(self, query: str = "") -> List[DiscoveredPlugin]:
        """Queries all sources and returns aggregated, normalized DiscoveredPlugin descriptors."""
        results: List[DiscoveredPlugin] = []
        for src in MarketplaceSource:
            if src == MarketplaceSource.LOCAL_BUILTIN:
                continue
            try:
                items = self.harvest_source(src, query)
                results.extend(items)
            except Exception as e:
                logger.warning("Harvester failed querying source %s: %s", src.value, e)
        return results

    def harvest_source(self, source: MarketplaceSource, query: str = "") -> List[DiscoveredPlugin]:
        """Harvests a single source."""
        if source in self._mock_sources:
            return self._normalize_items(source, self._mock_sources[source], query)

        # Real endpoints
        if source == MarketplaceSource.PYPI:
            return self._harvest_pypi(query)
        elif source == MarketplaceSource.OFFICIAL_MCP:
            return self._harvest_official_mcp(query)
        elif source == MarketplaceSource.GITHUB:
            return self._harvest_github(query)
        elif source == MarketplaceSource.GLAMA:
            return self._harvest_glama(query)
        elif source == MarketplaceSource.SMITHERY:
            return self._harvest_smithery(query)
        return []

    def _normalize_items(
        self, source: MarketplaceSource, raw_items: List[Dict[str, Any]], query: str
    ) -> List[DiscoveredPlugin]:
        normalized: List[DiscoveredPlugin] = []
        q_lower = query.lower()

        for item in raw_items:
            name = item.get("name", "")
            desc = item.get("description", "")
            tags = item.get("tags", [])

            if q_lower:
                match = (
                    q_lower in name.lower()
                    or q_lower in desc.lower()
                    or any(q_lower in t.lower() for t in tags)
                )
                if not match:
                    continue

            tagged_payload, nonce = encapsulate_untrusted_text(desc, source.value)

            plugin = DiscoveredPlugin(
                id=item.get("id", f"{source.value}:{name.lower().replace(' ', '-')}"),
                name=name,
                description=desc,
                source=source,
                execution_type=PluginExecutionType(
                    item.get("execution_type", PluginExecutionType.MCP_SERVER_STDIO.value)
                ),
                version=item.get("version", "1.0.0"),
                repository_url=item.get("repository_url", ""),
                author=item.get("author", ""),
                license=item.get("license", "MIT"),
                tags=tags,
                download_url=item.get("download_url", ""),
                integrity_hash=item.get("integrity_hash", ""),
                command=item.get("command", []),
                endpoint_url=item.get("endpoint_url", ""),
                environment_vars=item.get("environment_vars", {}),
                declared_permissions=item.get("declared_permissions", ["WORKSPACE_READ"]),
                untrusted_payload=tagged_payload,
                nonce=nonce,
                is_verified=item.get("is_verified", False),
                raw_metadata=item,
            )
            normalized.append(plugin)
        return normalized

    def _harvest_pypi(self, query: str) -> List[DiscoveredPlugin]:
        # PyPI JSON API: query search endpoint or specific package
        if not query:
            return []
        url = f"https://pypi.org/pypi/{urllib.parse.quote(query)}/json"
        try:
            data = self.http_client.get_json(url)
            info = data.get("info", {})
            name = info.get("name", query)
            desc = info.get("summary", "")
            tagged_desc, nonce = encapsulate_untrusted_text(desc, MarketplaceSource.PYPI.value)

            plugin = DiscoveredPlugin(
                id=f"pypi:{name.lower()}",
                name=name,
                description=desc,
                source=MarketplaceSource.PYPI,
                execution_type=PluginExecutionType.ISOLATED_CLI_PLUGIN,
                version=info.get("version", "1.0.0"),
                repository_url=info.get("project_urls", {}).get("Source", "") or info.get("home_page", ""),
                author=info.get("author", ""),
                license=info.get("license", "Unknown"),
                tags=info.get("classifiers", []),
                untrusted_payload=tagged_desc,
                nonce=nonce,
                command=["python", "-m", name],
                raw_metadata=info,
            )
            return [plugin]
        except Exception as e:
            logger.debug("PyPI harvest failed for %s: %s", query, e)
            return []

    def _harvest_official_mcp(self, query: str) -> List[DiscoveredPlugin]:
        # Official MCP Registry endpoint
        url = "https://registry.modelcontextprotocol.io/v0/servers"
        try:
            data = self.http_client.get_json(url)
            servers = data if isinstance(data, list) else data.get("servers", [])
            return self._normalize_items(MarketplaceSource.OFFICIAL_MCP, servers, query)
        except Exception as e:
            logger.debug("Official MCP harvest failed: %s", e)
            return []

    def _harvest_github(self, query: str) -> List[DiscoveredPlugin]:
        # GitHub search repositories with topic mcp-server
        search_term = f"topic:mcp-server {query}".strip()
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(search_term)}&sort=stars&order=desc"
        try:
            data = self.http_client.get_json(url)
            items = []
            for repo in data.get("items", []):
                items.append({
                    "id": f"gh:{repo.get('full_name', '')}",
                    "name": repo.get("name", ""),
                    "description": repo.get("description", "") or "",
                    "repository_url": repo.get("html_url", ""),
                    "author": repo.get("owner", {}).get("login", ""),
                    "license": (repo.get("license") or {}).get("spdx_id", "Unknown"),
                    "tags": repo.get("topics", []),
                    "execution_type": PluginExecutionType.MCP_SERVER_STDIO.value,
                    "command": ["npx", "-y", repo.get("name", "")],
                })
            return self._normalize_items(MarketplaceSource.GITHUB, items, query)
        except Exception as e:
            logger.debug("GitHub harvest failed: %s", e)
            return []

    def _harvest_glama(self, query: str) -> List[DiscoveredPlugin]:
        url = "https://glama.ai/api/mcp/servers"
        try:
            data = self.http_client.get_json(url)
            servers = data if isinstance(data, list) else data.get("servers", [])
            return self._normalize_items(MarketplaceSource.GLAMA, servers, query)
        except Exception as e:
            logger.debug("Glama harvest failed: %s", e)
            return []

    def _harvest_smithery(self, query: str) -> List[DiscoveredPlugin]:
        url = "https://smithery.ai/api/servers"
        try:
            data = self.http_client.get_json(url)
            servers = data if isinstance(data, list) else data.get("servers", [])
            return self._normalize_items(MarketplaceSource.SMITHERY, servers, query)
        except Exception as e:
            logger.debug("Smithery harvest failed: %s", e)
            return []
