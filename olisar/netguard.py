"""Outbound requests that must stay on the public internet.

An address is public when it's globally routable: loopback, private, link-local, the
shared 100.64.0.0/10 range (Tailscale's tailnet and its 100.100.100.100 service), reserved
and multicast addresses aren't. IPv6 forms that carry an IPv4 address inside them
(IPv4-mapped, IPv4-compatible, NAT64, 6to4, Teredo) are judged by that IPv4 address too,
since a host that routes them reaches it.

Checking a name isn't enough on its own. The name is resolved again when the connection is
made, and a DNS server that answers with a public address for the check and 127.0.0.1 for
the connection (rebinding) gets past it. ``resolve_public`` resolves once, off the event
loop, and ``PinnedTransport`` then connects to the address that was checked, whatever the
name resolves to by then. TLS still verifies the certificate against the name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore
import httpx

_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


class NonPublicHost(ValueError):
    """The host is, or resolves to, an address that isn't on the public internet."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    if any(ip in net for net in _NAT64) or ip in _IPV4_COMPATIBLE:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_address(address: str) -> bool:
    """True when ``address`` (an IP literal, optionally with an IPv6 zone) is globally
    routable. Anything that doesn't parse isn't."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_ipv4(ip)
        if inner is not None:
            candidates.append(inner)
    return all(c.is_global and not c.is_multicast for c in candidates)


def _addresses(host: str) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return [info[4][0] for info in infos]


def is_public_host(host: str) -> bool:
    """Blocking check that every address ``host`` resolves to is public. Use
    ``resolve_public`` on the event loop."""
    addresses = _addresses(host)
    return bool(addresses) and all(is_public_address(a) for a in addresses)


async def lookup(host: str) -> list[str]:
    """Every address ``host`` resolves to (an IP literal is its own), resolved in a worker
    thread so a slow DNS server can't stall the loop. Empty when it doesn't resolve."""
    return await asyncio.to_thread(_addresses, host)


def public_address(host: str, addresses: list[str]) -> str:
    """The address to connect to out of ``addresses``, what ``host`` resolved to. Raises
    ``NonPublicHost`` when there are none or any of them isn't public: there's no telling
    which one a connection would use."""
    if not addresses:
        raise NonPublicHost(f"{host} doesn't resolve")
    if not all(is_public_address(a) for a in addresses):
        raise NonPublicHost(f"{host} points at a private or local address")
    return addresses[0].split("%")[0]


async def resolve_public(host: str) -> str:
    """The address to connect to for ``host``: resolved off the event loop, and refused
    with ``NonPublicHost`` unless every address it has is public."""
    return public_address(host, await lookup(host))


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Opens TCP connections to one vetted address for one host, and refuses any other."""

    def __init__(self, host: str, address: str) -> None:
        self._host = host.lower()
        self._address = address
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host.lower() != self._host:
            raise httpcore.ConnectError(f"connection to {host} wasn't vetted")
        return await self._inner.connect_tcp(
            self._address, port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets aren't allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """An httpx transport that only connects to ``host``, at ``address``. No proxies (a
    proxy would make the connection somewhere else), no environment settings."""

    def __init__(self, host: str, address: str) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_PinnedBackend(host, address),
        )


__all__ = [
    "NonPublicHost", "PinnedTransport", "is_public_address", "is_public_host", "lookup",
    "public_address", "resolve_public",
]
