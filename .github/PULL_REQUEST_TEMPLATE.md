## 📝 技能贡献信息

- **技能名称**: `site-<name>`
- **技能类型**: [ ] 专用下载器卡 (A 表) / [ ] 站点知识卡 (B 表) / [ ] 通用方法论卡
- **目标站点/服务**: 
- **实测状态**: `✅ 已实测` / `🧪 未实测`
- **实测日期与环境**: 例如 `2026-09-06 (Linux x64, 海外/国内出口)`

---

## 🔍 自查清单 (Quality Gate)

- [ ] 符合 `docs/site-skills-spec.md` 或 `docs/targeted-downloader-spec.md` 结构标准
- [ ] 未向 `plugin/` 注入任何站点专有写死代码
- [ ] 包含基于 `probe_file` / 魔数 / 大小的文件完整性校验
- [ ] 已在 `.dsh/skills/site-directory.md` 登记更新
- [ ] 本地离线单测已跑通 (`node plugin/tests/run.mjs`)
