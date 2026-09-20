# AI 配图 + 真人朗读的解说类视频模板

从"康熙红票"这个项目里沉淀出来的可复用流程：一批 AI 生成的插画/历史照片 +
一段解说脚本，配上剪映朗读生成的语音，自动生成图片按真实语音节奏切换、
带运镜和转场的原生草稿。适用于历史/知识科普类图文解说视频。

## 这条链路做不到的事(边界)

- **不能自动生成语音**。剪映的"批量朗读"是账号在线资源，这个仓库的自动化
  故意不碰在线账号功能(参见 [独立 Skill](../README.md) 的边界说明)。语音
  必须你在剪映 UI 里手动朗读生成。
- **不能自动挑配图**。31 张左右的批量生成图默认按文件名/生成顺序分配给
  台词，不逐句核对画面内容是否贴切——如果对某几句的配图不满意，需要手动
  在 `special_images` 里指定精确匹配。
- **转场种类受限**。这个仓库的 `engine/native-resource-catalog.json` 目前
  只捕获验证过"叠化(dissolve)"一种转场资源。多样性只能靠硬切/短叠化/长
  叠化轮换,做不出划像、滑动等其他转场,除非先在剪映里手动做一次新转场、
  抓取资源登记进仓库。

## 完整流程

### 1. 准备素材和脚本

- 素材目录放这次要用的图片:AI 批量生成的插画(建议统一命名规律,比如都带
  数字序号)+ 若干张真实照片/文物图(用于关键人物/实物登场时插入)。
- 把解说脚本按"一句一行"整理好,自然分段用空行隔开。**先把这份脚本单独
  存成 txt 文件,永久保存(比如 `work/<项目名>-narration/blockN.txt`),
  不要只留在内存/临时对话里** —— 这是从"删了忘记保留原稿"这次教训里
  加的规矩,每一版脚本改动都单独存一份,不要覆盖。

### 2. 在剪映里手动朗读

- 把脚本文本分成几大块(每块几十句连续文字),在剪映里选中、点"批量朗读"
  生成语音。分块数量和边界你自己定,后面流程会自动探测实际分块点。
- 朗读生成后,语速/停顿由剪映决定(通常会应用一个如 1.3x 的速度系数让语音
  匹配你预留的片段长度)——不用管,后面会从草稿里读出真实的目标时长和源
  时长。

### 3. 把语音和草稿里的真实分块信息导出来

用 `engine/headless_runtime.helper()._decrypt_metadata_in_memory` 读草稿
`draft_info.json`,在 `materials.audios` 里找 `type: text_to_audio` 的条目,
记录:
- `path` 对应的本机 wav 文件(在草稿目录 `textReading/` 下,复制出来永久
  保存,不要留在会被后续 `build`/`publish` 覆盖的草稿目录里)
- 该音频片段的 `target_timerange.duration`(时间轴上的目标时长,已经是
  应用过速度系数之后的)和 `source_timerange.duration`(原始语速时长)

**关键教训**:这里要用的参考文本,必须是**你朗读时实际使用的文本**(可能
临场改过措辞),不是更早的草稿版本。用错文本去对齐,匹配率会明显下降,
且越到后面偏差越大。

### 4. 跑对齐脚本生成 plan.json

写一份 `spec.json`(格式见下),然后:

```bash
python3 SKILL/scripts/build_narrated_image_plan.py --spec spec.json --out WORK/plan.json
```

这一步会自动:
1. 用 Whisper(本地开源 ASR,首次用需要 `pip3 install openai-whisper`;如果
   在有 TLS 中间人代理的公司网络下证书报错,再装 `pip3 install
   pip-system-certs` 让 Python 信任系统证书链)转写每段语音,拿到逐字时间戳。
2. 把你提供的参考文本和 Whisper 识别结果做编辑距离对齐,推算每一句台词在
   录音里的真实起止时间,再除以速度系数换算回时间轴时间。
3. 按比例把非特殊台词分配给生成的插画(按文件名数字序号排序)。
4. 把 `special_images` 指定的真实照片按精确文本匹配插入到正确位置——如果
   某张要插入的图片所在的那组台词横跨了插入点前后,会自动拆成两段,保证
   时间顺序不错乱(这是"利玛窦图片提前 3.3 秒出现"那个 bug 修复后的行为,
   千万不要把这段拆分逻辑简化掉)。
5. 生成 8 种 Ken Burns 缩放/平移模式轮换,缩放和平移幅度按比例配平,保证
   平移到画面边缘时不会露黑边(平移幅度必须明显小于 `(缩放倍数-1)/2` 留出
   的余量,这是"横向平移黑边"那次踩坑后加的安全边际)。
6. 转场三段轮换:硬切 / 0.33s 叠化 / 0.53s 叠化。

`spec.json` 格式:

```json
{
  "schema": "jy14-narrated-image-video-spec/v1",
  "name": "本次视频名",
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "materials_dir": "/绝对路径/素材目录",
  "blocks": [
    {"text_file": "/绝对路径/block1.txt", "wav_file": "/绝对路径/block1.wav",
     "target_duration_us": 101733333},
    {"text_file": "/绝对路径/block2.txt", "wav_file": "/绝对路径/block2.wav",
     "target_duration_us": 90833333}
  ],
  "generated_images": {"glob": "ChatGPT Image*.png", "sort": "numeric-suffix"},
  "special_images": [
    {"match_line": "台词原文,精确匹配某一行", "image": "某张真实照片.jpg"},
    {"match_lines": ["连续两行", "都算这张图的"], "image": "另一张图.png",
     "keyframes": {"scale": [1.35, 1.62], "x": [0.10, -0.14], "y": [0, 0]}}
  ]
}
```

`target_duration_us` 是第 3 步从草稿里读出来的音频片段目标时长;
`whisper_json` 字段可省略,脚本会自动转写并缓存在 wav 同目录下,下次
重跑直接复用缓存,不用重新转写。`match_line`/`match_lines` 必须和
`block*.txt` 里的原文逐字一致,找不到会直接报错(不会静默瞎猜位置)。

### 5. build / verify-build / publish

和普通无界面草稿完全一样,见 [无界面原生草稿](headless-macos.md)。

注意 `build` 生成的 plan 不含字幕轨(这条链路默认只处理图片+语音节奏)。
如果要加字幕,在这之后单独用 `edit` 系列命令,或另外扩展 `spec.json` 加一条
`text` 轨。

### 6. 发布时的已知坑

- **发草稿前必须完全退出剪映**(Cmd+Q,不是切后台)。曾经因为剪映还开着,
  中途删除旧草稿文件夹导致剪映在重新打开时在原路径新建了一个空白项目
  顶替,内容全丢——不是这条链路的 bug,是操作顺序问题。
- 反复 `build`+`publish` 覆盖同名草稿时,如果上一次是直接 `rm -rf` 删除
  的旧草稿文件夹(而不是通过剪映自身删除),`root_meta_info.json` 首页索引
  里会留一条指向不存在路径的失效记录,下次 `publish` 会报
  `Draft registration conflicts`。可以重新打开一次剪映让它自动清理,或者
  用与 `engine/jy14_headless.py::publish()` 相同的加锁/校验/原子替换方式
  手动摘除那一条失效记录(只删 `draft_fold_path` 已经不存在的那条,不要
  批量清)。
- 如果这次改动涉及 `engine/` 下的核心文件,记得同步更新
  `skills/yichen-jianying-edit/scripts/headless_draft.py` 里 `PINS` 字典
  对应的哈希,否则会被"核心文件被改动"的完整性校验拦下来。
