# 给 Claude Code 的项目说明

这是 Jianying Headless 的私有核心仓库（剪映专业版 macOS 无界面自动化）。详见 [README.md](README.md)。

## 做"AI 配图 + 朗读解说视频"这类任务时

用户描述"这堆图片配这段语音剪个视频"之类需求时，先读：

- [skills/yichen-jianying-edit/SKILL.md](skills/yichen-jianying-edit/SKILL.md) — Skill 总入口
- [skills/yichen-jianying-edit/references/ai-narrated-image-video.md](skills/yichen-jianying-edit/references/ai-narrated-image-video.md) — 完整工作流和脚本说明都在这里，不在此文件重复

## 硬性安全规则（就算没触发上面那个 Skill，这条也要遵守）

**每次删除/覆盖任何已存在的草稿文件夹之前，必须紧挨着那个删除动作本身，
先跑 `python3 skills/yichen-jianying-edit/scripts/require_jianying_closed.py`。**
返回非零就停下来，提示用户完全退出剪映（Cmd+Q）再重试——不能只信任更早时候检查过的结果，
用户可能在两次操作之间的空隙重新打开剪映。

真实事故：曾经因为剪映还开着时删除重建草稿文件夹，用户手动加的内容（4K 水印、模板生成的内容）
连同剪映自己的 `.backup/` 快照一起被永久删除，无法恢复。

## 其他约定

- `work/` 目录不进 git（本地临时文件、素材备份、构建产物），改动前不用担心里面的东西被提交。
- 涉及 `engine/` 下核心文件的改动，记得同步更新 `skills/yichen-jianying-edit/scripts/headless_draft.py`
  里 `PINS` 字典对应的哈希，否则会被完整性校验拦下。
- Bash 权限自动放行的 hook 配置在 `.claude/`（本机专属，未提交）。
