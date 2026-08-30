# Resource Hub — 图形验证码识别技能(`skills/captcha`)

本地 OCR(ddddocr)为主的验证码识别,带**检测裁剪 + 多变体投票 + 跨模型一致性 + 置信度三态**,
可选 **VLM 兜底**(本地 Ollama Qwen2.5-VL 等)。对齐计划文档 §4.4 的"全免费验证码路线"。

```
验证码图 → 墨迹检测 → 检测定位文字框(裁剪/逐字) → 12 个预处理变体 → std/beta 双模型投票
        → 三态结果: solved(≥0.9) / uncertain / none → 低置信可选 VLM 兜底
```

## 安装与运行

依赖已装在 `resource-hub-research/venv-demo`(ddddocr + opencv + numpy + Pillow)。用它的 Python:

```powershell
# 识别单张/多张
& .\resource-hub-research\venv-demo\Scripts\python.exe -m skills.captcha.cli solve captcha.png
& .\resource-hub-research\venv-demo\Scripts\python.exe -m skills.captcha.cli solve a.png b.png --charset digits --json

# 批量评测:文件夹内图片以「答案.后缀」命名
& .\resource-hub-research\venv-demo\Scripts\python.exe -m skills.captcha.cli bench bench_data/hard --charset digits

# 生成合成扭曲验证码(测稳定性的标准工具)
& .\resource-hub-research\venv-demo\Scripts\python.exe -m skills.captcha.tools.generate_captchas --out bench_data --difficulty all --count 20
```

## 实测能力(合成扭曲集,20 张/档,随机种子固定可复现)

| 场景 | easy | medium | hard | extreme |
|---|---|---|---|---|
| **纯数字**(最常见) | 100% | 95% | 70% | 50% |
| **字母+数字**(忽略大小写) | 70% | 80% | 50% | 55% |
| 原始 ddddocr(对照,alnum 精确) | 15% | 25% | 20% | 10% |

> 合成集刻意做得比多数真实站点更狠(随机大小写 + 大旋转 + 波形扭曲 + 干扰线/噪点)。
> 真实站点里:纯数字/纯小写验证码表现接近 easy~medium 档;**带重试后稳定性大幅提升**:

**重试 3 次有效成功率**(站点支持刷新验证码,`solve_with_retry` 已实现):
- digits hard: 70% → **97.3%**;extreme: 50% → **87.5%**
- alnum medium(忽略大小写): 80% → **99.2%**

**诚实边界**:当 std+beta 两个模型**一致地认错**(如 `6031`→`6037`),本地没有任何信号能发现 ——
这是纯本地 OCR 的天花板,重度扭曲的正确出路是 VLM 兜底或人工介入一次(计划 §4.4 同款设计)。

## 作为 Agent 技能怎么用(核心 API)

```python
from skills.captcha import CaptchaSolver, SolveStatus

solver = CaptchaSolver(charset="0123456789")   # 纯数字站:字符集提示,精度最高
r = solver.solve(captcha_bytes)
if r.status == SolveStatus.SOLVED:
    submit(r.text)                             # 高置信,直接用
elif r.status == SolveStatus.UNCERTAIN:
    retry_or_vlm()                             # 刷新验证码重试 / VLM / 人工
else:
    refresh_captcha()                          # 没识别出来,直接换图
```

带重试的稳定入口(每次从站点抓新图):

```python
from skills.captcha import solve_with_retry

r = solve_with_retry(fetch_image=lambda: page.get_captcha_bytes(), max_attempts=3)
```

## 三个关键设计

1. **检测裁剪**:大图(文字只占一角,如 384×344 里一行小字)先用 det 模型定位文字框,
   单框裁剪识别、多框逐字拼接 —— 治"全图喂 OCR 被空白背景带偏";
2. **保真度加权投票**:raw/gray/去噪等无损变体权重大,otsu/morph 等有损二值化权重小
   (二值化会腐蚀细笔画导致"掉字符",如 `2651` 被认成 `265`);
3. **跨模型一致性**:std+beta 双模型,忽略大小写后内容一致才算强信号;
   两模型分歧 → 强制 `uncertain`,宁可不提交也不提交错的;
   单票无旁证 → 置信封顶 0.85,防模型在空白图上的幻觉(如 beta 把纯白图读成「一」)。

## 可选 VLM 兜底(重度扭曲/中文验证码)

配置环境变量后自动启用(纯 urllib,零依赖),本地 Ollama 装 Qwen2.5-VL 即可:

```ini
CAPTCHA_VLM_URL=http://localhost:11434/v1/chat/completions
CAPTCHA_VLM_MODEL=qwen2.5-vl:7b
```

中文验证码(如调研样本 yzm2,std/beta 分别读出 胸/困 互相矛盾)正是 VLM 的用武之地 ——
本地 OCR 会诚实返回 `uncertain`,而不是瞎提交。

## 测试

```powershell
& .\resource-hub-research\venv-demo\Scripts\python.exe -m unittest tests.test_captcha -v
```

覆盖:预处理管线形状、yzm1 已知样本(3n3d)、空白图不误判、合成易档可解、字符集限制。
