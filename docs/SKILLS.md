# Resource Hub 技能开发与安装卸载指南（mod 式管理）

技能 = `skills/<name>/` 目录 + `skill.json` 元数据。**像 mod 一样装/卸/启停**，
由 `skills/manager.py` 驱动（工具级生效：停用的工具不进 LLM catalog、调用被拒绝）。

## 一、已有技能清单

| 技能 | 提供工具 | 说明 |
|---|---|---|
| `mail` | `mail_code` | 邮箱收码(IMAP 轮询收验证码/安全链接) |
| `captcha` | `captcha`、`slide_captcha` | 图形验证码:字符 OCR + VLM 兜底 + **滑块拼图** |
| `security` | `scan_file` | 下载查毒(ClamAV + YARA + 启发式) |
| `streaming` | `download_stream` | 音视频大文件流式下载(分段并发/HLS) |
| `universal` | `universal_download` | 万能下载(播放页找流/CF 反制/m3u8 合并) |
| `adblock` | (无,纯数据池) | 广告过滤(EasyList 系开源规则) |

核心工具（`download`/`search`/`analyze_page`/`verify_file` 等）属于引擎本体，不可卸载。

## 二、安装 / 卸载 / 启停（命令）

```bash
# 查看所有技能与工具注册状态
python -m skills.manager list

# 安装一个技能包(从外部路径拷贝到 skills/,自动启用其工具)
python -m skills.manager install ./my_skill

# 卸载(停用其工具 + 目录移出到 skills_archive/,重新 install 可恢复)
python -m skills.manager uninstall security

# 启停(工具级生效:停用后 LLM 目录里看不到、invoke 被拒)
python -m skills.manager disable streaming
python -m skills.manager enable streaming
```

状态持久化在 `skills/.state.json`；重启进程后 `skills/__init__.py` 自动恢复禁用状态。

## 三、怎么写一个新技能（模板）

```
my_skill/
├── __init__.py        # 对外 API(纯规则能力)
├── skill.json         # 元数据:声明提供哪些工具
├── core.py            # 实现(可选,按需分文件)
├── cli.py             # CLI(可选): python -m skills.my_skill.cli ...
└── README.md          # 文档(可选但推荐)
```

**`skill.json`**（必填）：
```json
{
  "name": "my_skill",
  "version": "0.1.0",
  "description": "一句话说明这个技能做什么",
  "tools": ["my_tool_name"],
  "requires": []
}
```

**工具注册**：在任意被 import 的模块里用 `@tool` 装饰器（`skills/core.py` 的 DSH 式注册表），
工具名与 `skill.json` 的 `tools` 声明一致（卸载时据此停用）：

```python
# my_skill/tools.py(或直接放在 ai/skills.py / agent/tools.py 等注册点)
from skills.core import tool, ToolResult

@tool("my_tool_name", "工具描述(给 LLM 看)", parameters={...}, category="execute")
def _my_tool(...) -> ToolResult:
    ...
```

**要求**：
1. `__init__.py` 只做对外 API，不硬编码注册工具（工具注册放注册点模块，与现有
   `ai/skills.py`、`agent/tools.py` 风格一致）；
2. 依赖可选时用**惰性 import + 明确报错**（如滑块需要 ddddocr/opencv），不要 import 期崩溃；
3. 网络请求统一走 `proxy.py`（RH_PROXY_URL）；敏感配置走 `.env`（`load_dotenv`）；
4. 附测试（`tests/test_<skill>.py`），本地起 ThreadingHTTPServer 模拟，不依赖外网。

## 四、卸载时的行为

- `uninstall` = `disable`（停用 `skill.json` 声明的所有工具）+ 目录移出到 `skills_archive/`；
- 已 import 的模块工具注册不会被撤销，但 `catalog()` 不再暴露、`invoke()` 返回
  「工具已停用」—— 对 AI 与脚本调用完全隔离；
- `install` 恢复 = 拷贝回 `skills/` + `enable`。
