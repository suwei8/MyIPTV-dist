#!/usr/bin/env python3
"""把 suwei8/blog 的 iptv.txt(DIYP格式) 转成标准 ios.m3u 发布到同仓库.

- #genre# -> group-title
- name,url -> #EXTINF 条目
- 跳过非 http(s) 行(migu:// 伪协议仅 MyIPTV App 端侧可解析;
  同名 IPv4 备用行仍保留, 频道不丢)

secret: BLOG_TOKEN (对 suwei8/blog 有 contents:write 权限的 PAT)
"""

import base64
import json
import os
import sys
import urllib.request

REPO = "suwei8/blog"
UA = "ios-m3u-builder/1.0"


def gh(method, path, token, payload=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA},
        data=json.dumps(payload).encode() if payload else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main():
    token = os.environ.get("BLOG_TOKEN")
    if not token:
        sys.exit("BLOG_TOKEN not set")

    meta = gh("GET", "iptv.txt", token)
    content = base64.b64decode(meta["content"]).decode()

    out = ["#EXTM3U"]
    group = ""
    for raw in content.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.endswith("#genre#"):
            group = s.split(",", 1)[0].strip()
            continue
        if "," not in s:
            continue
        name, url = s.rsplit(",", 1)
        name, url = name.strip(), url.strip()
        if not url.startswith("http"):
            continue  # migu:// 等伪协议
        if "pan.13982.com" in url:
            url = url.replace("&direct=1", "").replace("?direct=1&", "?").replace("?direct=1", "")
            # 去 direct 后 /d/ 返回 wky-dy 中继链: 服务端注 Referer, iOS 播放器可播
        out.append(f'#EXTINF:-1 group-title="{group}",{name}')
        out.append(url)
    m3u = "\n".join(out) + "\n"

    old = gh("GET", "ios.m3u", token)
    if old and base64.b64decode(old["content"]).decode() == m3u:
        print("ios.m3u unchanged")
        return

    payload = {"message": "chore: rebuild ios.m3u",
               "content": base64.b64encode(m3u.encode()).decode()}
    if old:
        payload["sha"] = old["sha"]
    gh("PUT", "ios.m3u", token, payload)
    print(f"ios.m3u committed, {len(out)//2} entries")


if __name__ == "__main__":
    main()
