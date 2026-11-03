#!/usr/bin/env python3
"""把 VLESS_URL 分享链接转换成 sing-box 客户端配置(输出到 stdout).

用法:
  VLESS_URL='vless://uuid@host:port?type=ws&path=/&host=xxx&security=...#name' \
      python3 tools/vless_to_singbox.py > /tmp/sb.json

本地监听 127.0.0.1:10808 (mixed inbound, http+socks5 均可),
供 update_iptv.py 的 CHECK_PROXY 使用.
"""

import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse

LISTEN_PORT = int(os.environ.get("PROXY_PORT", "10808"))


def main():
    url = os.environ.get("VLESS_URL", "").strip()
    if not url.startswith("vless://"):
        sys.exit("VLESS_URL missing or not a vless:// link")

    u = urlparse(url)
    uuid = u.username
    server = u.hostname
    port = u.port or 443
    q = {k: v[0] for k, v in parse_qs(u.query).items()}

    outbound = {
        "type": "vless",
        "tag": "out",
        "server": server,
        "server_port": port,
        "uuid": uuid,
    }
    if q.get("encryption", "none") not in ("", "none"):
        outbound["encryption"] = q["encryption"]

    security = q.get("security", "")
    if security in ("tls", "reality"):
        outbound["tls"] = {"enabled": True}
        if security == "reality":
            outbound["tls"]["reality"] = {
                "enabled": True,
                "public_key": q.get("pbk", ""),
                "short_id": q.get("sid", ""),
            }
        if q.get("sni"):
            outbound["tls"]["server_name"] = q["sni"]
        if q.get("fp"):
            outbound["tls"]["utls"] = {"enabled": True, "fingerprint": q["fp"]}
        if q.get("flow"):
            outbound["flow"] = q["flow"]

    ntype = q.get("type", "tcp")
    if ntype == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": unquote(q.get("path", "/")),
            "headers": {"Host": q.get("host", server)},
        }
    elif ntype == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": q.get("serviceName", ""),
        }
    elif ntype != "tcp":
        sys.exit(f"unsupported transport type: {ntype}")

    config = {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "in",
                "listen": "127.0.0.1",
                "listen_port": LISTEN_PORT,
            }
        ],
        "outbounds": [outbound],
    }
    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
