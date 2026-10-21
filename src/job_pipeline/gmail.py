import email
import email.policy
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from .fetch import SOURCE_PATH, canonical_url, detect_source

def extract_html_from_eml(path: str) -> str:
    with open(path, 'rb') as f:
        eml = f.read()
    # # 第 1 步：把 bytes 解析成一个「邮件对象」
    msg = email.message_from_bytes(eml, policy=email.policy.default)
    # # 第 2 步：从邮件对象中提取 HTML 内容
    html_part = msg.get_body(preferencelist=("html",))
    # # 第 3 步：返回 HTML 内容
    return html_part.get_content() if html_part else ""

def extract_job_links(html: str) -> list[str]:
    urls = re.findall(r'href="([^"]+)"', html)
    jobs: list[str] = []
    for url in urls:
        # 先检检查domain是否是seek，然后检查url是否是job链接
        pattern = SOURCE_PATH.get(detect_source(url))
        if pattern and re.search(pattern, urlparse(url).path):  # urlparse(url).path更准确：只在路径里搜索
            jobs.append(canonical_url(url))
    return list(dict.fromkeys(jobs))  # remove duplicates 并且按照顺序 set 无序去重
