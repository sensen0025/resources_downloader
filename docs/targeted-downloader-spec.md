# 特定网站针对性资源检索：专用下载器接入规范

> 版本 1.0 · 2026-09-06 · 与 docs/site-skills-spec.md 配套

## 0. 问题与定位

"特定网站的针对性资源检索" = 对某站点用**该站成熟专用下载器**直接把资源拿下来，
例如 B站 → **BBDown**、千站通用视频 → **yt-dlp**、国内长视频 → **lux**、图站 → **gallery-dl**。
成熟工具由社区持续适配站点登录/风控/格式合并，比"每次现场写爬虫"稳得多、维护成本最低。

本规范统一这类工具卡的写法、站点↔工具注册与降级纪律。**专用工具仍是可选可执行依赖**，
本仓库不持有/二次开发任何站点专用常驻代码；LLM 负责：选站→查注册表→装/探测→调参→
下载→校验→记忆→必要时降级。

## 1. 优先级（每次先想这条链）

```
记忆/目录(有无同类记录) → 专用下载器(bbdown/yt-dlp/…) → 站点知识卡(API/直链)
→ 通用爬虫(write-and-run-crawler) → browser/人工(web 终端未上，转凭证/用户)
```
只要注册表里有该站专用工具且能用，**禁止跳过它去现写爬虫**；工具失败(版本/接口过期)才降级，
并把失败原因写 memory。

## 2. 文件与命名

- 卡：`.dsh/skills/site-<scope>-<tool>.md`（如 `site-bilibili-bbdown`、`site-videos-yt-dlp`）。
- 注册表：`.dsh/skills/site-directory.md`（加列：首选专用工具 / 状态）。
- 工具本体：**不入库**，装宿主机；卡里给安装方式与依赖（如 bbdown 混流需 ffmpeg）。

## 3. 工具卡固定章节（顺序不可乱）

1. **定位**：站点 ↔ 工具；为什么用它（能力/适配面）。
2. **安装与依赖**：官方渠道（release 二进制 / `dotnet tool install` / `pip install`）+ 系统依赖；
   给出"装到本机即可用"的检查命令（`<tool> --version`）。
3. **鉴权前置**：免费量/登录量；Cookie/access_token 怎么给（如 `-c "SESSDATA=…"`、`login` 扫码）；
   未鉴权能拿什么画质。
4. **探测**：先用"仅解析不下载"确认 URL 可解析、看到画质/分P列表（如 `-info/--show-all`）。
5. **标准命令模板**：单条/列表/指定分P/画质优先/输出目录/文件命名；变量按实际工具文档填。
6. **常见坑**：混流缺 ffmpeg、接口过期、海外出口被拒、限流与并发、文件名冲突、需登录 404。
7. **输出与校验**：产物目录与命名规则 → `probe_file`（mp4 `ftyp`/mkv/音频魔数）+ 大小/时长核对。
8. **合规**：工具作者免责声明；个人学习用途；会员内容需账号权限。
9. **记忆建议**：tags（`video` `专用下载器名` `login`…）与 methods 记录（用哪个版本/参数有效、
   哪天失败降级）。

## 4. 质量门（提交前自查）
- 命令/参数逐条对照**工具官方 README**（标注核对日期）；参数名不凭记忆。
- 本环境跑不通的写 `🧪 未实测(日期, 原因)`；跑通的打 `✅ (日期+出口)`。
- 注册表同步：站点行更新"首选工具/状态"。

## 5. 站点↔工具注册表（初版，工具卡按需补）

| 站点族 | 首选工具 | 安装 | 卡状态 |
|---|---|---|---|
| 哔哩哔哩 bilibili | BBDown (nilaoda) | `dotnet tool install --global BBDown` 或 GitHub release；需 ffmpeg | ✅ site-bilibili-bbdown |
| YouTube / 千站通用视频 | yt-dlp | `pip install yt-dlp` | 🧪 待建卡 |
| 国内长视频(优酷/爱奇艺/芒果/腾讯等) | lux（CN 维护 fork）或 yt-dlp | release/`go install` | 🧪 待建卡 |
| 图片站/画廊(twitter/instagram/pixiv/danbooru…) | gallery-dl | `pip install gallery-dl` | 🧪 待建卡 |
| 音乐(Spotify/YT Music) | spotdl / yt-dlp | `pip install spotdl` | 🧪 待建卡 |

> 新增站点/工具：先在目录加行（含许可证与用途边界），再按本规范建卡。
