import base64
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

from googleapiclient.discovery import build

ALERT_QUERY = "from:(jobmail@s.seek.co.nz OR jobalerts-noreply@linkedin.com)"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]   # 只读，最小权限
CREDENTIALS_FILE = ROOT / "credentials.json"   # 「我是谁」：你的应用（client）
TOKEN_FILE = ROOT / "token.json"               #  硬盘上的一个文件（Path 对象）读 creds
# creds 内存里的凭证对象（Credentials），它知道「我过期了没有、还有没有效」

def gmail_service():
    """登录并返回 Gmail 服务台（service），其他函数都用它来调用 API。"""
    return build("gmail", "v1", credentials=get_credentials())

# 读取邮件 从gmail down下来的 .eml文件
def extract_html_from_eml(path: str) -> str:
    with open(path, 'rb') as f:
        return extract_html_from_bytes(f.read())
    
# 原始邮件的byte -> 解码成html, .eml文件和Gmail API都走这里
def extract_html_from_bytes(raw: bytes) -> str:
    # # 第 1 步：把 bytes 解析成一个「邮件对象」
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    # # 第 2 步：从邮件对象中提取 HTML 内容
    html_part = msg.get_body(preferencelist=("html",))
    # # 第 3 步：返回 HTML 内容
    return html_part.get_content() if html_part else ""

# 搜索 处理分页
def search_alert_ids(service, query: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token
        ).execute()
        # resp → "messages" 列表 → 每一项是 {"id": ..., "threadId": ...}。我们只要 id
        ids += [m["id"] for m in resp.get("messages", [])]
        page_token = resp.get('nextPageToken')  # 有就返回 token，没有（最后一页）就返回 None
        if not page_token:
            break
    return ids

# 取一封邮件：ID → 原始邮件 → HTML
def fetch_message_html(service, msg_id: str) -> str:
    """凭邮件 ID 取回整封邮件，解码成 HTML。"""
    msg = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
    raw = base64.urlsafe_b64decode(msg["raw"])  # base64url 字符串 → 原始邮件 bytes（和 .eml 文件内容一样）
    return extract_html_from_bytes(raw)          # 复用 .eml 的解析逻辑


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