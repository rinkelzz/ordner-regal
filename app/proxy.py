"""Echte Absenderadresse hinter einem Reverse Proxy.

X-Forwarded-For/-Proto werden nur übernommen, wenn die TCP-Gegenstelle ein vertrauenswürdiger Proxy ist: immer die
NAS selbst (DSM-Reverse-Proxy, 127.0.0.1/::1), dazu die unter Verwaltung → System eingetragenen Adressen/Netze
(z. B. ein eigener Nginx Proxy Manager im LAN) und ORG_TRUSTED_PROXIES. Bei einer Kette zählt von rechts die erste
Adresse, die selbst kein vertrauenswürdiger Proxy ist – mitgeschickte Fantasie-Einträge weiter links bleiben wirkungslos.
"""
import ipaddress
import os
from typing import List, Tuple, Union

Net = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
ALWAYS = [ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128")]
_extra: List[Net] = []


def parse(text: str) -> Tuple[List[Net], List[str]]:
    """„192.168.90.26, 10.0.0.0/24“ → Netze; ungültige oder zu weite Einträge als Fehlerliste."""
    nets, errors = [], []
    for part in (text or "").replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            errors.append(f"„{part}“ ist keine IP-Adresse und kein Netz")
            continue
        if net.prefixlen < (16 if net.version == 4 else 48):
            errors.append(f"„{part}“ ist zu weit gefasst – bitte nur die Adresse des Proxys (oder ein kleines Netz)")
            continue
        if net not in nets:
            nets.append(net)
    return nets, errors


def configure(text: str) -> None:
    global _extra
    nets, _ = parse(",".join(x for x in (os.environ.get("ORG_TRUSTED_PROXIES", ""), text or "") if x))
    _extra = nets


def is_trusted(host: str) -> bool:
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in n for n in ALWAYS + _extra if n.version == ip.version)


def is_loopback(host: str) -> bool:
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def env_entries() -> str:
    return os.environ.get("ORG_TRUSTED_PROXIES", "")


class ProxyHeaders:
    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if client and is_trusted(client[0]):
                headers = {k.lower(): v for k, v in scope.get("headers", [])}
                xff = headers.get(b"x-forwarded-for", b"").decode("latin-1")
                if xff:
                    hops = [h.strip() for h in xff.split(",") if h.strip()]
                    real = next((h for h in reversed(hops) if not is_trusted(h)), hops[0] if hops else "")
                    try:
                        ipaddress.ip_address(real.split("%")[0])
                        scope["client"] = (real, 0)
                    except ValueError:
                        pass
                proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0].strip().lower()
                if proto in ("http", "https"):
                    scope["scheme"] = proto if scope["type"] == "http" else ("wss" if proto == "https" else "ws")
        await self.app(scope, receive, send)
