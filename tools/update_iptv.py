#!/usr/bin/env python3
"""自动维护 suwei8/blog 仓库中的 iptv.txt.

流程:
  1. 通过 GitHub Contents API 读取 iptv.txt
  2. 并发检测所有直播源(m3u8)有效性(网盘 mp4 链接跳过)
  3. 对失效源, 从上游聚合列表中按频道名匹配候选, 实测后替换
  4. 有变更则写回 blog 仓库

需要的 secret:
  BLOG_TOKEN  - 对 suwei8/blog 有 contents:write 权限的 PAT
  VLESS_URL   - (CI 专用) 国内代理节点, workflow 里转成 sing-box 配置

环境变量:
  CHECK_PROXY - 可选, 直播源检测走该代理(如 http://127.0.0.1:10808).
                GitHub API 与上游列表拉取始终直连, 只有源检测走代理.
"""

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

BLOG_REPO = "suwei8/blog"
IPTV_PATH = "iptv.txt"
TIMEOUT = 10
WORKERS = 20
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MyIPTV-Checker/1.0"

# 不检测不替换的域名(自建网盘点播资源, 长期有效)
SKIP_DOMAINS = ("pan.13982.com",)

# 候选源池: 第三方维护/每日自动更新的直播源聚合列表
UPSTREAMS = [
    # 每日自动检测更新的央视/卫视列表, 每频道多候选
    "https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_cctv.m3u8",
    "https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_all.m3u8",
    # 综合直播源列表, 更新频繁, 酒店源/IPTV 源较多
    "https://raw.githubusercontent.com/yoursmile66/TVBox/main/live.txt",
    "https://raw.githubusercontent.com/BurningC4/Chinese-IPTV/master/TV-IPV4.m3u",
    "https://raw.githubusercontent.com/TianmuTNT/iptv/main/iptv.m3u",
    # IPv4 综合列表, 每日自动更新
    "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.txt",
    "https://live.zbds.top/tv/iptv4.txt",
    # iptv-org 中国列表(m3u 格式)
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    # 酒店源自动扫描, 每日更新
    "https://raw.githubusercontent.com/ssili126/tv/main/itvlist.txt",
    # IPv6 源, 质量好但需要网络支持 IPv6(实测会过滤)
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u",
]

# 频道名归一化后的别名映射: 我们的 key -> 上游可能出现的 key
ALIASES = {
    "cctv1": ["cctv1", "cctv1综合"],
    "cctv4": ["cctv4", "cctv4中文国际", "cctv4国际", "cctv4欧洲", "cctv4美洲"],
    "cctv6": ["cctv6", "cctv6电影"],
    "cctv9": ["cctv9", "cctv9纪录"],
    "cctv13": ["cctv13", "cctv13新闻"],
    "chc电影": ["chc电影", "chc高清电影", "chc影院", "chc动作电影"],
}


CHECK_PROXY = os.environ.get("CHECK_PROXY")
if CHECK_PROXY:
    _check_opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": CHECK_PROXY, "https": CHECK_PROXY})
    )
else:
    _check_opener = urllib.request.build_opener()
_direct_opener = urllib.request.build_opener()


def norm(name):
    """频道名归一化, 用于跨列表匹配."""
    n = name.lower()
    n = re.sub(r"[\s\-_·（）()\[\]【】/]", "", n)
    for w in ("高清", "超清", "hd", "4k", "1080p", "720p", "频道"):
        n = n.replace(w, "")
    m = re.match(r"cctv(\d+)", n)
    if m:
        return "cctv" + str(int(m.group(1)))
    return n.replace("台", "")


def http_get(url, timeout=TIMEOUT, max_bytes=None, proxied=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    opener = _check_opener if proxied else _direct_opener
    with opener.open(req, timeout=timeout) as r:
        data = r.read() if max_bytes is None else r.read(max_bytes)
        return r.status, data


def check_url(url):
    """验证直播源可用性.

    返回 (ok, latency_ms). 对 m3u8 会进一步验证首个分片可下载.
    """
    start = time.time()
    try:
        status, body = http_get(url, max_bytes=1024 * 1024)
        if status != 200 or not body:
            return False, -1
        text = body.decode("utf-8", "ignore")
        if "#EXTM3U" in text:
            # master playlist -> 取一个子流; media playlist -> 取一个分片
            seg = None
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    seg = line
                    break
            if not seg:
                return False, -1
            seg_url = urljoin(url, seg)
            if seg_url != url:
                s2, b2 = http_get(seg_url, max_bytes=256 * 1024)
                if s2 != 200 or not b2:
                    return False, -1
                # 若是二级 playlist 再取一次分片
                t2 = b2.decode("utf-8", "ignore")
                if "#EXTM3U" in t2:
                    for line in t2.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            s3, b3 = http_get(
                                urljoin(seg_url, line), max_bytes=256 * 1024
                            )
                            return (s3 == 200 and len(b3) > 0), int(
                                (time.time() - start) * 1000
                            )
                    return False, -1
        return True, int((time.time() - start) * 1000)
    except Exception:
        return False, -1


def fetch_url_text(url):
    status, body = http_get(url, timeout=20, proxied=False)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return body.decode("utf-8", "ignore")


def build_candidates():
    """抓取上游列表, 建立 归一化频道名 -> [候选url, ...]."""
    index = {}
    for up in UPSTREAMS:
        try:
            text = fetch_url_text(up)
        except Exception as e:
            print(f"[upstream] fetch failed {up}: {e}")
            continue
        count = 0
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # txt 格式: 名称,URL ; m3u 格式: #EXTINF...名称 \n URL
        for i, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                name = line.rsplit(",", 1)[-1]
                if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                    u = lines[i + 1]
                    if u.startswith("http"):
                        index.setdefault(norm(name), []).append(u)
                        count += 1
            elif line.startswith("#"):
                continue
            elif ",#genre#" in line:
                continue
            elif "," in line:
                name, u = line.rsplit(",", 1)
                if u.startswith("http"):
                    index.setdefault(norm(name), []).append(u)
                    count += 1
        print(f"[upstream] {up} -> {count} entries")
    # 去重
    for k in index:
        index[k] = list(dict.fromkeys(index[k]))
    return index


def gh_api(method, url, token, payload=None):
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        },
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    token = os.environ.get("BLOG_TOKEN")
    if not token:
        sys.exit("BLOG_TOKEN not set")

    if CHECK_PROXY:
        # 代理自检: 确认出口在国内. 代理挂了直接失败, 避免海外出口误判源死活
        try:
            status, body = http_get(
                "http://ip-api.com/json?fields=query,country,isp", proxied=True
            )
            info = json.loads(body.decode())
            print(
                f"[proxy] exit={info.get('query')} {info.get('country')} {info.get('isp')}"
            )
            if info.get("country") != "China":
                sys.exit(f"proxy exit is not China: {info}")
        except Exception as e:
            sys.exit(f"CHECK_PROXY unreachable: {e}")

    api = f"https://api.github.com/repos/{BLOG_REPO}/contents/{IPTV_PATH}"
    meta = gh_api("GET", api, token)
    sha = meta["sha"]
    content = base64.b64decode(meta["content"]).decode("utf-8")
    lines = content.splitlines()

    # 收集需要检测的行
    check_items = []  # (line_no, name, url)
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.endswith("#genre#") or "," not in s:
            continue
        name, url = s.rsplit(",", 1)
        host = urlparse(url).netloc
        if any(d in host for d in SKIP_DOMAINS):
            continue
        check_items.append((i, name.strip(), url.strip()))

    print(f"checking {len(check_items)} live sources...")
    alive = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {
            ex.submit(check_url, url): (i, name, url)
            for i, name, url in check_items
        }
        for f in as_completed(futs):
            i, name, url = futs[f]
            ok, ms = f.result()
            alive[i] = ok
            print(f"  {'OK ' if ok else 'DEAD'} {name} {url[:70]} ({ms}ms)")

    dead = [(i, name, url) for i, name, url in check_items if not alive[i]]
    if not dead:
        print("all sources alive, nothing to do")
        return

    print(f"{len(dead)} dead sources, searching replacements...")
    index = build_candidates()

    changes = []
    unresolved = []

    def candidates_for(name):
        key = norm(name)
        keys = ALIASES.get(key, [key])
        out = []
        for k in keys:
            out += index.get(k, [])
        seen, res = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                res.append(u)
        return res

    def fix_one(item):
        i, name, url = item
        for cand in candidates_for(name):
            ok, ms = check_url(cand)
            if ok:
                return i, name, url, cand, ms
        return i, name, url, None, -1

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, name, old, new, ms in ex.map(fix_one, dead):
            if new:
                lines[i] = f"{name},{new}"
                changes.append(f"{name}: {old} -> {new}")
                print(f"  REPLACED {name} -> {new[:70]} ({ms}ms)")
            else:
                unresolved.append(name)
                print(f"  UNRESOLVED {name} (kept dead url)")

    if not changes:
        print("no replacements found; file unchanged")
        if unresolved:
            print("still dead: " + ", ".join(unresolved))
        return

    new_content = "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    if new_content == content:
        print("no changes")
        return

    msg = "chore(iptv): auto-update dead live sources\n\n" + "\n".join(
        changes[:50]
    )
    if unresolved:
        msg += "\n\nunresolved: " + ", ".join(unresolved)
    gh_api(
        "PUT",
        api,
        token,
        {
            "message": msg,
            "content": base64.b64encode(new_content.encode()).decode(),
            "sha": sha,
        },
    )
    print(f"committed {len(changes)} replacements to {BLOG_REPO}")
    if unresolved:
        print("unresolved (still dead): " + ", ".join(unresolved))

    # GitHub Actions step summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## iptv.txt auto-update\n\n")
            f.write(f"- replaced: **{len(changes)}**\n")
            for c in changes:
                f.write(f"  - `{c}`\n")
            if unresolved:
                f.write(f"- still dead: {', '.join(unresolved)}\n")


if __name__ == "__main__":
    main()
