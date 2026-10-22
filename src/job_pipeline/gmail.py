import email
import email.policy
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from .fetch import SOURCE_PATH, canonical_url, detect_source

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]   # 只读，最小权限
CREDENTIALS_FILE = ROOT / "credentials.json"   # 「我是谁」：你的应用（client）
TOKEN_FILE = ROOT / "token.json"               #  硬盘上的一个文件（Path 对象）读 creds
# creds 内存里的凭证对象（Credentials），它知道「我过期了没有、还有没有效」

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

# Gmail OAuth 登录：向 Google 证明「这个程序可以只读你的邮箱」，拿到一把临时钥匙 Credentials。真正干活的不是这段函数本身，而是它返回的 creds，后面会拿去调 Gmail API
def get_credentials() -> Credentials:
    creds = None
    # 之前登陆过吗？读已经保存的token，项目根目录已经有 token.json从里面读出 creds，不用再开浏览器
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    # 2. 过期了但能刷新 → 刷新
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    # 3. 没有 token，或者无效 → 打开浏览器登录，文件本身不知道里面的 token 有没有过期，只有把它读成 creds 对象之后才能判断
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

    # 4. 存回去，下次就不用再登录了
    TOKEN_FILE.write_text(creds.to_json())
    return creds