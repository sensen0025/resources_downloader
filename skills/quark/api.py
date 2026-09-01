"""夸克网盘 API 客户端(requests 同步实现,对齐 QuarkPan / QuarkPanTool / musicdl 实测链路)。

分享解析链路(全部实测过的官方 HTTP 接口,无需浏览器/扫码):
1. POST /share/sharepage/token        → stoken(分享访问令牌,匿名可拿)
2. GET  /share/sharepage/detail       → 分享内文件列表(fid / share_fid_token / 目录)
3. POST /share/sharepage/save         → 转存到自己的网盘(需要登录 Cookie)→ task_id
4. GET  /task?task_id=...             → 轮询转存任务完成 → save_as_top_fids
5. POST /file/download                → 由已转存 fid 换取直链 download_url
6. GET  download_url                  → 带夸克 UA/Cookie/Referer 下载落地

Cookie 说明:
- 无 Cookie:只能拿文件列表(stoken 匿名);
- 有 Cookie(`__puus`):可转存+下载 —— 一次导入,以后全自动,不再扫码。
- 每次响应里的 Set-Cookie(__puus/__pus)自动合并回 Cookie,续期不失效。

对齐 Alist 的 __puus 刷新经验:下载 URL 的签名基于请求 /file/download 时的
Cookie 生成,下载请求头必须与之一致,否则 403 —— 因此本客户端在换取直链的
同一次会话里复用同一份 Cookie 快照。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

__all__ = ["QuarkClient", "QuarkError", "QuarkAuthError", "DEFAULT_HEADERS"]

# 分享 API 主端点(按实测可用度排序,逐个回退)
SHARE_HOSTS = ("drive.quark.cn", "drive-h.quark.cn", "drive-pc.quark.cn")
BASE_HOST = "drive-pc.quark.cn"          # 文件/任务/转存
ACCOUNT_URL = "https://pan.quark.cn/account/info"

API_UA = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/94.0.4606.71 Safari/537.36 "
    "Core/1.94.225.400 QQBrowser/12.2.5544.400"
)
# 下载直链时必须用夸克客户端 UA(服务器按 UA 校验签名,见 QuarkPanTool)
DL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
    "Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"
)

DEFAULT_HEADERS = {
    "user-agent": API_UA,
    "origin": "https://pan.quark.cn",
    "referer": "https://pan.quark.cn/",
    "accept-language": "zh-CN,zh;q=0.9",
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
}

DEFAULT_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}


class QuarkError(Exception):
    """夸克 API 错误(带可读 message)。"""


class QuarkAuthError(QuarkError):
    """需要登录/登录态失效。"""


def _ts() -> str:
    return str(int(time.time() * 1000))


def _dt() -> str:
    return str(random.randint(100, 9999))


class QuarkClient:
    """夸克网盘 HTTP 客户端(requests 同步)。

    cookie: 登录 Cookie 字符串(含 __puus 即可);为空则只能浏览分享目录。
    on_cookie_refresh: 可选回调,响应 Set-Cookie 带来新 __puus/__pus 时调用
        (用于把续期后的 Cookie 持久化回存储)。
    """

    def __init__(self, cookie: str = "", timeout: float = 30.0,
                 on_cookie_refresh: Optional[Callable[[str], None]] = None) -> None:
        self.cookie = (cookie or "").strip()
        self.timeout = timeout
        self.on_cookie_refresh = on_cookie_refresh
        self._session = requests.Session()

    # ------------------------------------------------------------ 基础请求

    def _headers(self, cookie: Optional[str] = None) -> dict:
        h = dict(DEFAULT_HEADERS)
        ck = self.cookie if cookie is None else cookie
        if ck:
            h["cookie"] = ck
        return h

    def _params(self, **kw) -> dict:
        p = dict(DEFAULT_PARAMS)
        p.update({"__dt": _dt(), "__t": _ts()})
        p.update(kw)
        return p

    def _merge_set_cookie(self, resp: requests.Response) -> None:
        """合并响应 Set-Cookie 里的 __puus/__pus(续期),并回调持久化。"""
        if not self.cookie:
            return
        sc = resp.headers.get("Set-Cookie", "")
        if not sc:
            return
        merged = self.cookie
        for part in sc.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, rest = part.partition("=")
            name = name.strip()
            if name in ("__puus", "__pus"):
                val = rest.split(";")[0].strip()
                if val:
                    merged = _set_cookie_kv(merged, name, val)
        if merged != self.cookie:
            old = self.cookie
            self.cookie = merged
            if self.on_cookie_refresh:
                try:
                    self.on_cookie_refresh(merged)
                except Exception:
                    pass
            # 静默丢弃:仅当没有并发新值时回滚(对齐 Alist 刷新逻辑)

    def _request(self, method: str, url: str, *, params: Optional[dict] = None,
                 json_data: Optional[dict] = None, cookie: Optional[str] = None,
                 headers: Optional[dict] = None, allow_auth_error: bool = False) -> dict:
        try:
            resp = self._session.request(
                method, url, params=params, json=json_data,
                headers=headers or self._headers(cookie),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise QuarkError(f"网络请求失败: {type(e).__name__}: {str(e)[:120]}")
        self._merge_set_cookie(resp)
        if resp.status_code == 401 or (resp.status_code == 403 and not allow_auth_error):
            raise QuarkAuthError(f"夸克返回 HTTP {resp.status_code}: 登录态失效或需要登录")
        try:
            data = resp.json()
        except ValueError:
            raise QuarkError(f"响应不是 JSON(HTTP {resp.status_code}): {resp.text[:120]}")
        if isinstance(data, dict):
            status = data.get("status")
            code = data.get("code")
            message = str(data.get("message") or "")
            if status not in (200, None) or (code not in (0, None)):
                low = message.lower()
                if any(k in low for k in ("login", "auth", "登录", "未登录", "登录已过期")):
                    raise QuarkAuthError(f"夸克鉴权错误: {message}")
                raise QuarkError(f"夸克 API 错误(status={status} code={code}): {message}")
        return data

    def _share_request(self, method: str, path: str, *, params: Optional[dict] = None,
                       json_data: Optional[dict] = None, cookie: Optional[str] = None) -> dict:
        """分享端点:多主机回退(实测 drive.quark.cn / drive-h / drive-pc 均可)。"""
        last_err: Optional[Exception] = None
        for host in SHARE_HOSTS:
            try:
                return self._request(method, f"https://{host}/1/clouddrive{path}",
                                     params=params, json_data=json_data, cookie=cookie,
                                     headers=self._headers(cookie))
            except (QuarkAuthError, QuarkError) as e:
                last_err = e
                if isinstance(e, QuarkAuthError):
                    raise
        raise QuarkError(f"分享接口全部主机失败: {last_err}")

    # ------------------------------------------------------------ 认证

    def is_logged_in(self) -> bool:
        """Cookie 是否有效(调 account/info)。网络异常/无 Cookie 一律 False。"""
        if not self.cookie:
            return False
        try:
            resp = self._session.get(
                ACCOUNT_URL,
                params={"fr": "pc", "platform": "pc"},
                headers=self._headers(),
                timeout=self.timeout,
            )
            self._merge_set_cookie(resp)
            if resp.status_code != 200:
                return False
            data = resp.json()
            return bool(data.get("data") and data["data"].get("nickname"))
        except Exception:
            return False

    # ------------------------------------------------------------ 分享解析

    def get_stoken(self, pwd_id: str, passcode: str = "") -> str:
        """拿分享访问令牌(匿名可拿,不需要登录)。"""
        data = self._share_request(
            "POST", "/share/sharepage/token",
            json_data={
                "pwd_id": pwd_id,
                "passcode": passcode or "",
                "support_visit_limit_private_share": True,
            },
        )
        token = (data.get("data") or {}).get("stoken") or ""
        if not token:
            raise QuarkError(f"获取分享令牌失败: {data.get('message') or 'stoken 为空'}")
        return token

    def list_share_files(self, pwd_id: str, stoken: str, pdir_fid: str = "0",
                         page: int = 1, size: int = 50) -> tuple[list[dict], int]:
        """列分享目录一页。返回 (文件列表, 总条数)。"""
        params = self._params(
            pwd_id=pwd_id, stoken=stoken, pdir_fid=pdir_fid, force="0",
            _page=str(page), _size=str(size),
            _fetch_banner="1", _fetch_share="1", _fetch_total="1",
            _sort="file_type:asc,file_name:asc",
        )
        data = self._share_request("GET", "/share/sharepage/detail", params=params)
        meta = data.get("metadata") or {}
        total = int(meta.get("_total") or 0)
        return list((data.get("data") or {}).get("list") or []), total

    def walk_share(self, pwd_id: str, stoken: str, max_depth: int = 3,
                   size_cap: int = 500) -> list[dict]:
        """递归列全分享(含子目录)。返回扁平文件列表,每项带 path 字段。

        目录项 dir=True 时继续深入;文件项保留 fid/share_fid_token/size 供转存。
        """
        out: list[dict] = []
        page_size = 50

        def rec(pdir_fid: str, path: str, depth: int) -> None:
            if depth > max_depth or len(out) >= size_cap:
                return
            page = 1
            while len(out) < size_cap:
                try:
                    items, total = self.list_share_files(pwd_id, stoken, pdir_fid, page)
                except Exception:
                    break
                if not items:
                    break
                for it in items:
                    if len(out) >= size_cap:
                        break
                    name = str(it.get("file_name") or "未命名")
                    is_dir = bool(it.get("dir"))
                    item = dict(it)
                    item["path"] = f"{path}/{name}" if path else name
                    out.append(item)
                    if is_dir and depth < max_depth:
                        rec(str(it.get("fid") or ""), item["path"], depth + 1)
                total = int(total or 0)
                if page * page_size >= total:
                    break
                page += 1
                if page > 20:
                    break

        rec("0", "", 1)
        return out

    # ------------------------------------------------------------ 转存 + 直链

    def save_share(self, pwd_id: str, stoken: str, pdir_fid: str,
                   fid_list: list[str], fid_token_list: list[str],
                   to_pdir_fid: str = "0") -> str:
        """转存分享文件到自己的网盘。返回 task_id。"""
        data = self._share_request(
            "POST", "/share/sharepage/save",
            json_data={
                "fid_list": fid_list,
                "fid_token_list": fid_token_list,
                "to_pdir_fid": to_pdir_fid,
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "scene": "link",
            },
        )
        task_id = (data.get("data") or {}).get("task_id") or ""
        if not task_id:
            raise QuarkError(f"转存任务创建失败: {data.get('message') or 'task_id 为空'}")
        return task_id

    def wait_task(self, task_id: str, timeout: float = 60.0) -> dict:
        """轮询任务直到完成(status=2)或失败(status=3)。"""
        deadline = time.monotonic() + timeout
        retry = 0
        while time.monotonic() < deadline:
            params = self._params(task_id=task_id, retry_index=str(retry))
            try:
                data = self._request("GET", f"https://{BASE_HOST}/1/clouddrive/task",
                                     params=params)
            except QuarkError:
                retry += 1
                time.sleep(1.0)
                continue
            td = data.get("data") or {}
            status = td.get("status")
            if status == 2:
                return td
            if status == 3:
                raise QuarkError(f"转存任务失败: {td.get('message') or '未知'}")
            retry += 1
            time.sleep(1.0)
        raise QuarkError(f"转存任务超时(>{int(timeout)}s)")

    def get_download_urls(self, fids: list[str]) -> list[dict]:
        """已转存文件 → 直链(需登录 Cookie;签名绑定请求时的 Cookie/UA)。"""
        if not self.cookie:
            raise QuarkAuthError("没有登录 Cookie,无法换取直链(先导入 Cookie)")
        params = self._params(sys="win32", ve="2.5.56", ut="", guid="")
        data = self._request(
            "POST", f"https://{BASE_HOST}/1/clouddrive/file/download",
            params=params, json_data={"fids": fids},
            headers={
                **self._headers(),
                "user-agent": DL_UA,
            },
        )
        return list(data.get("data") or [])

    def delete_files(self, fids: list[str]) -> bool:
        """删除自己网盘里的文件(转存清理用,best-effort)。"""
        if not self.cookie or not fids:
            return False
        try:
            data = self._request(
                "POST", f"https://{BASE_HOST}/1/clouddrive/file/delete",
                params=self._params(),
                json_data={"action_type": 1, "exclude_fids": [], "filelist": fids},
            )
            return bool(data.get("code") in (0, None))
        except Exception:
            return False

    # ------------------------------------------------------------ 直链下载

    def download_url(self, url: str, save_path: str | Path, *,
                     filename: Optional[str] = None,
                     progress: Optional[Callable[[int, int], None]] = None,
                     retries: int = 2) -> Path:
        """下载直链到本地(带夸克 UA/Cookie/Referer —— 签名校验必需)。"""
        dest = Path(save_path)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / (filename or url.split("?")[0].rsplit("/", 1)[-1] or "quark_download.bin")
        headers = {
            "user-agent": DL_UA,
            "referer": "https://pan.quark.cn/",
            "origin": "https://pan.quark.cn",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
        }
        if self.cookie:
            headers["cookie"] = self.cookie
        last_err = ""
        for attempt in range(max(1, retries)):
            try:
                with self._session.get(url, headers=headers, timeout=self.timeout * 4,
                                       stream=True) as resp:
                    if resp.status_code == 403 and self.cookie:
                        # 403 常因下载请求头与换链时不一致 → 重试一次(同 UA/Cookie)
                        if attempt + 1 < max(1, retries):
                            continue
                        raise QuarkError("直链下载 403(签名失效,请重新解析)")
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length") or 0)
                    done = 0
                    tmp = dest / (target.name + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if chunk:
                                f.write(chunk)
                                done += len(chunk)
                                if progress and total:
                                    progress(done, total)
                    tmp.replace(target)
                    return target
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {str(e)[:100]}"
        raise QuarkError(f"直链下载失败: {last_err or '未知错误'}")

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> "QuarkClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _set_cookie_kv(cookie: str, name: str, value: str) -> str:
    """在 Cookie 字符串里替换/追加一个键值对。"""
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    out = []
    replaced = False
    for p in parts:
        k = p.split("=", 1)[0].strip()
        if k == name:
            out.append(f"{name}={value}")
            replaced = True
        else:
            out.append(p)
    if not replaced:
        out.append(f"{name}={value}")
    return "; ".join(out)


def cookie_to_dict(cookie: str) -> dict[str, str]:
    """Cookie 字符串 → dict(供直接拼 Cookie 头/调试)。"""
    d: dict[str, str] = {}
    for p in cookie.split(";"):
        p = p.strip()
        if "=" in p:
            k, _, v = p.partition("=")
            d[k.strip()] = v.strip()
    return d
