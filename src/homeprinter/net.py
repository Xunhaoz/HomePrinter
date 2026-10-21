"""Network helpers shared by the printer and scanner clients."""

import asyncio
import ipaddress
import socket


async def resolve_ipv4(host: str, port: int) -> str:
    """IPv4 address for host. A .local name also resolves to an IPv6
    link-local address, which aiohttp can't reach without a scope id."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, family=socket.AF_INET)
    return infos[0][4][0]
