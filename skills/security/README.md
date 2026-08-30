# Resource Hub — 下载安全查毒技能(`skills/security`)

下载文件**安全性校对**:爬取/下载的资源在交给用户前先过安全闸门。
引擎 = **开源杀毒 ClamAV**(clamd 守护进程 / clamscan 命令行)+ **YARA 规则**(可选)+
**内置启发式**(纯标准库,永远可用)。对齐计划文档的「下载 → 验证」链路,与
`verify_file`(内容/格式验证)互补:**verify_file 管对不对,scan_file 管安不安全**。

```
下载完成 → scan_file 安全闸门 → 通过 → verify_file 内容验证 → 交付用户
              │
              ├─ infected(检出病毒)   → 删除文件,换源重下
              ├─ suspicious(可疑)     → 默认删除(严格模式),strict=false 放行但标记
              └─ unknown(无杀毒引擎)  → 放行,但诚实标注"仅启发式覆盖,建议装 ClamAV"
```

## 开源杀毒引擎怎么装(ClamAV)

| 平台 | 安装 | 备注 |
|---|---|---|
| Windows | `winget install Cisco.ClamAV` | 装完把 `clamd.exe`/`clamscan.exe` 所在目录加进 PATH |
| macOS | `brew install clamav` | |
| Ubuntu/Debian | `sudo apt install clamav clamav-daemon` | |
| Fedora | `sudo dnf install clamav clamav-update` | |

装完三步:

```powershell
# 1. 更新病毒库(签名数据库,首次必做)
freshclam

# 2. 启动守护进程 clamd(推荐:病毒库常驻内存,扫描最快,默认监听 127.0.0.1:3310)
#    Windows: clamd.exe(或注册为服务)   Linux/macOS: sudo systemctl start clamav-daemon

# 3. 验证链路:本技能生成 EICAR 测试病毒并扫描,能检出即打通
python -m skills.security.cli eicar
```

> 没装 clamd 也能用:`clamscan` 命令行模式自动兜底(逐文件启动进程,慢一点)。
> 两个都没有时,扫描器返回 `verdict=unknown`,仅启发式覆盖 —— 流程不阻塞,
> 但 AI 会诚实标注覆盖不足,建议补装 ClamAV。

## 用法(CLI)

```bash
# 查看引擎可用性(clamd 在不在跑 / clamscan 在不在 PATH / yara 装没装)
python -m skills.security.cli engines

# 扫描文件/目录(多引擎并行)
python -m skills.security.cli scan downloads/xxx.litematic
python -m skills.security.cli scan downloads/ --json
python -m skills.security.cli scan x.jpg --engine clamav heuristic   # 指定引擎子集
python -m skills.security.cli scan x.zip --rules my_rules/           # 追加 YARA 规则
```

退出码:`0`=全部干净,`1`=检出病毒/可疑,`2`=无引擎可用或出错,`3`=参数错误。

## 作为 Agent 技能怎么用(核心 API)

```python
from skills.security import scan_file, detect_engines

r = scan_file("downloads/foo.litematic")
print(r.verdict)   # clean / infected / suspicious / unknown
print(r.ok)        # 安全闸门判定(clean/unknown=True)
for f in r.findings:
    print(f.engine, f.status, f.detail)   # 各引擎明细,可审计

if r.verdict == "infected":
    # 必须删除换源,绝不可交付
    Path(r.path).unlink()
elif r.verdict == "suspicious":
    # 默认拒绝;人工复核后可用 strict=False 放行
    scan_file(p, strict=False)
```

AI 侧:工具 `scan_file` 已在 `ai/skills.py` 注册(`import ai` 即进 AgentCore 目录),
下载完成后模型按提示词调用;任务层 `fetch_resource(security_scan=True)`(默认开)
在直链下载和浏览器 Agent 落地后自动执行闸门,检出即删除换源。

## 四引擎设计

1. **clamd(ClamAV 守护进程)** — 原生 socket 协议(INSTREAM 流式上传,不落盘、
   不依赖 clamd 文件系统权限),零第三方依赖(Python 标准库 `socket`/`struct`);
2. **clamscan(ClamAV 命令行)** — clamd 不可用时的兜底;目录扫描走 `clamscan -r`;
3. **YARA(可选)** — `pip install yara-python` 后自动启用;内置 `rules/resource_hub.yar`
   (JS/PowerShell/VBS 下载器、certutil/bitsadmin 滥用、双重扩展名、PE/ELF 伪装),
   `meta.severity=high` 判 infected,medium/low 判 suspicious;
4. **heuristic(内置启发式,纯标准库)** — 魔法字节 vs 扩展名伪装(如 `.jpg` 实为
   MZ/ELF/zip)、压缩炸弹(zip 中央目录判定,不解压)、脚本载荷特征(PowerShell
   编码命令/下载器,非文档类文件里出现)、双重扩展名/尾随空格。

## 测试

```bash
python -m unittest tests.test_security -v
```

覆盖:伪装可执行文件、压缩炸弹、脚本载荷、双重扩展名、干净文件放行、无引擎时
unknown 不阻塞、`scan_file` 工具注册与参数校验(无需真装 ClamAV)。
