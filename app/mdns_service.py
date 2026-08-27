"""v6.0 Phase 2 — mDNS service discovery for LAN mode.

Advertises _billbook._tcp.local. on port 8000 when lan_mode is enabled.
Uses zeroconf (free, LGPL).
"""
import logging
import socket

logger = logging.getLogger(__name__)

_mdns_service = None


def start_mdns(port: int = 8000, name: str = "BillBook", version: str = "v6.0"):
    """Start advertising the BillBook service on the local network."""
    global _mdns_service
    try:
        from zeroconf import Zeroconf, ServiceInfo
        import zeroconf as _zc
    except ImportError:
        logger.warning("zeroconf not installed — mDNS discovery unavailable. "
                       "Install with: pip install zeroconf")
        return

    if _mdns_service is not None:
        return  # Already running

    # Get the local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    _mdns_service = Zeroconf()
    info = ServiceInfo(
        type_="_billbook._tcp.local.",
        name=f"BillBook._billbook._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties={
            "name": name,
            "version": version,
            "pairing": "1",
        },
        server=f"billbook.local.",
    )
    try:
        _mdns_service.register_service(info)
        logger.info("mDNS service registered: _billbook._tcp.local. on %s:%d", local_ip, port)
    except Exception as e:
        logger.warning("mDNS registration failed: %s", e)
        _mdns_service = None


def stop_mdns():
    """Stop advertising the BillBook service."""
    global _mdns_service
    if _mdns_service is not None:
        try:
            _mdns_service.unregister_all_services()
            _mdns_service.close()
            logger.info("mDNS service stopped")
        except Exception as e:
            logger.warning("mDNS shutdown error: %s", e)
        _mdns_service = None
